"""
Jottask Billing Module
Stripe integration for subscriptions
"""

import os
import stripe
from flask import Blueprint, request, redirect, url_for, session, jsonify
from supabase import create_client, Client
from auth import login_required

billing_bp = Blueprint('billing', __name__, url_prefix='/billing')

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

# Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Price IDs (set these in your Stripe dashboard)
PRICE_IDS = {
    'pro_monthly': os.getenv('STRIPE_PRICE_PRO_MONTHLY'),
    'pro_yearly': os.getenv('STRIPE_PRICE_PRO_YEARLY'),
    'business_monthly': os.getenv('STRIPE_PRICE_BUSINESS_MONTHLY'),
    'business_yearly': os.getenv('STRIPE_PRICE_BUSINESS_YEARLY'),
}

APP_URL = os.getenv('APP_URL', 'http://localhost:5000')


def get_or_create_stripe_customer(user_id, email):
    """Get existing Stripe customer or create new one"""
    # Check if user already has a Stripe customer ID
    user = supabase.table('users').select('stripe_customer_id').eq('id', user_id).single().execute()

    if user.data and user.data.get('stripe_customer_id'):
        return user.data['stripe_customer_id']

    # Create new Stripe customer
    customer = stripe.Customer.create(
        email=email,
        metadata={'user_id': user_id}
    )

    # Save to database
    supabase.table('users').update({
        'stripe_customer_id': customer.id
    }).eq('id', user_id).execute()

    return customer.id


@billing_bp.route('/checkout/<plan>')
@login_required
def create_checkout_session(plan):
    """Create Stripe Checkout session for subscription"""
    user_id = session['user_id']
    user_email = session['user_email']

    # Map plan to price ID
    price_id = PRICE_IDS.get(plan)
    if not price_id:
        return jsonify({'error': 'Invalid plan'}), 400

    try:
        customer_id = get_or_create_stripe_customer(user_id, user_email)

        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{APP_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{APP_URL}/billing/cancelled',
            metadata={
                'user_id': user_id,
                'plan': plan
            }
        )

        return redirect(checkout_session.url, code=303)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@billing_bp.route('/portal')
@login_required
def customer_portal():
    """Redirect to Stripe Customer Portal for subscription management"""
    user_id = session['user_id']

    user = supabase.table('users').select('stripe_customer_id').eq('id', user_id).single().execute()

    if not user.data or not user.data.get('stripe_customer_id'):
        return redirect(url_for('settings'))

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=user.data['stripe_customer_id'],
            return_url=f'{APP_URL}/settings'
        )
        return redirect(portal_session.url, code=303)

    except Exception as e:
        return redirect(url_for('settings'))


@billing_bp.route('/success')
@login_required
def checkout_success():
    """Handle successful checkout"""
    session_id = request.args.get('session_id')

    if session_id:
        try:
            checkout_session = stripe.checkout.Session.retrieve(session_id)
            # Subscription is already updated via webhook, just show success
        except:
            pass

    return redirect(url_for('settings', message='Subscription activated successfully!'))


@billing_bp.route('/cancelled')
@login_required
def checkout_cancelled():
    """Handle cancelled checkout"""
    return redirect(url_for('settings', message='Checkout cancelled'))


@billing_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks"""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        handle_checkout_completed(session_data)

    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        handle_subscription_updated(subscription)

    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_deleted(subscription)

    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        handle_payment_failed(invoice)

    return jsonify({'received': True})


def handle_checkout_completed(session_data):
    """Handle successful checkout completion"""
    user_id = session_data.get('metadata', {}).get('user_id')
    plan = session_data.get('metadata', {}).get('plan', 'pro')
    subscription_id = session_data.get('subscription')

    if not user_id:
        return

    # Determine tier from plan name
    tier = 'starter' if 'starter' in plan else 'pro'

    supabase.table('users').update({
        'subscription_status': 'active',
        'subscription_tier': tier,
        'stripe_subscription_id': subscription_id
    }).eq('id', user_id).execute()

    print(f"✅ Subscription activated for user {user_id}: {tier}")

    # Handle referral rewards
    try:
        # Check if this user was referred
        referral = supabase.table('referrals').select('*').eq('referred_id', user_id).eq('reward_given', False).execute()

        if referral.data and len(referral.data) > 0:
            ref = referral.data[0]
            referrer_id = ref['referrer_id']

            # Give both users $5 credit
            REFERRAL_CREDIT = 5.00

            # Credit the referrer
            referrer = supabase.table('users').select('referral_credits').eq('id', referrer_id).single().execute()
            current_credits = referrer.data.get('referral_credits', 0) if referrer.data else 0
            supabase.table('users').update({
                'referral_credits': current_credits + REFERRAL_CREDIT
            }).eq('id', referrer_id).execute()

            # Credit the referred user
            referred = supabase.table('users').select('referral_credits').eq('id', user_id).single().execute()
            referred_credits = referred.data.get('referral_credits', 0) if referred.data else 0
            supabase.table('users').update({
                'referral_credits': referred_credits + REFERRAL_CREDIT
            }).eq('id', user_id).execute()

            # Mark referral as converted and reward given
            supabase.table('referrals').update({
                'status': 'converted',
                'reward_given': True,
                'converted_at': 'now()'
            }).eq('id', ref['id']).execute()

            print(f"🎁 Referral rewards given: {referrer_id} and {user_id} each got ${REFERRAL_CREDIT}")

    except Exception as e:
        print(f"⚠️ Error processing referral reward: {e}")


def handle_subscription_updated(subscription):
    """Handle subscription updates (upgrades, downgrades)"""
    customer_id = subscription['customer']

    # Find user by Stripe customer ID
    user = supabase.table('users').select('id').eq('stripe_customer_id', customer_id).single().execute()

    if not user.data:
        return

    status = subscription['status']
    if status == 'active':
        db_status = 'active'
    elif status in ['past_due', 'unpaid']:
        db_status = 'active'  # Still give access but flag for follow-up
    elif status == 'canceled':
        db_status = 'cancelled'
    else:
        db_status = status

    supabase.table('users').update({
        'subscription_status': db_status
    }).eq('id', user.data['id']).execute()

    print(f"📝 Subscription updated for user {user.data['id']}: {db_status}")


def handle_subscription_deleted(subscription):
    """Handle subscription cancellation"""
    customer_id = subscription['customer']

    user = supabase.table('users').select('id').eq('stripe_customer_id', customer_id).single().execute()

    if not user.data:
        return

    supabase.table('users').update({
        'subscription_status': 'expired',
        'subscription_tier': 'starter'
    }).eq('id', user.data['id']).execute()

    print(f"❌ Subscription cancelled for user {user.data['id']}")


def handle_payment_failed(invoice):
    """Handle failed payment"""
    customer_id = invoice['customer']

    user = supabase.table('users').select('id, email').eq('stripe_customer_id', customer_id).single().execute()

    if user.data:
        # You could send an email notification here
        print(f"⚠️ Payment failed for user {user.data['id']}")


# ============================================
# PRICING PAGE DATA
# ============================================

PLANS = [
    {
        'id': 'free_trial',
        'name': 'Free Trial',
        'price_monthly': 0,
        'price_yearly': 0,
        'features': [
            '14 days free',
            '20 tasks per month',
            'Email-to-task creation',
            'Smart reminders',
            'Projects & checklists'
        ],
        'cta': 'Start Free Trial'
    },
    {
        'id': 'starter',
        'name': 'Starter',
        'price_monthly': 8,
        'price_yearly': 80,
        'popular': True,
        'features': [
            '100 tasks per month',
            'Email-to-task creation',
            'Smart reminders',
            'Projects & checklists',
            'Daily summary emails',
            'Email support'
        ],
        'cta': 'Get Started'
    },
    {
        'id': 'pro',
        'name': 'Pro',
        'price_monthly': 15,
        'price_yearly': 150,
        'features': [
            'Unlimited tasks',
            'Everything in Starter',
            'Priority email processing',
            'Advanced AI features',
            'Priority support',
            'Early access to new features'
        ],
        'cta': 'Go Pro'
    }
]


def get_pricing_data():
    """Get pricing data for display"""
    return PLANS
