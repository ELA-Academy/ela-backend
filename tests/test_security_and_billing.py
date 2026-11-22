"""
Automated security, RBAC, teacher-login, and billing/Stripe correctness tests.
Works with both Python's built-in unittest and pytest:
    cd backend
    ./venv/Scripts/python.exe -m unittest -v tests/test_security_and_billing.py
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

# Configure isolated in-memory SQLite test environment BEFORE importing app
os.environ['EVENTLET_NO_PATCH'] = '1'
os.environ['DISABLE_BACKGROUND_THREADS'] = '1'
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['SECRET_KEY'] = 'test-secret-key-for-unittest-32bytes!'
os.environ['JWT_SECRET_KEY'] = 'test-jwt-secret-key-for-unittest-32bytes!'
os.environ['STRIPE_WEBHOOK_SECRET'] = 'whsec_test_secret'

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask_jwt_extended import create_access_token
from app import create_app, socketio
from app.models import db
from app.models.super_admin_model import SuperAdmin
from app.models.staff_model import Staff
from app.models.department_model import Department
from app.models.student_model import Student, Parent
from app.models.financial_model import (
    StudentFinancialAccount, Invoice, InvoiceItem, Payment, ProcessedStripeEvent
)
from app.models.login_otp_model import LoginOTP


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


class TestSecurityAndBilling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context():
            db.drop_all()
            db.create_all()

            dept_accounting = Department(name='Accounting', dashboard_route='/admin/accounting')
            dept_admissions = Department(name='Admissions', dashboard_route='/admin/admissions')
            dept_programs = Department(name='Programs')
            db.session.add_all([dept_accounting, dept_admissions, dept_programs])
            db.session.flush()

            sa = SuperAdmin(name='Founder Admin', email='founder@ela.org', is_active=True)
            sa.set_password('Password123!')

            acct_staff = Staff(name='Alice Accountant', email='alice@ela.org', role='staff', is_active=True)
            acct_staff.set_password('Password123!')
            acct_staff.departments.append(dept_accounting)

            prog_staff = Staff(name='Bob Programs', email='bob@ela.org', role='staff', is_active=True)
            prog_staff.set_password('Password123!')
            prog_staff.departments.append(dept_programs)

            teacher = Staff(name='Clara Teacher', email='clara@ela.org', role='teacher', is_active=True)
            teacher.set_password('Password123!')

            parent = Parent(first_name='Pat', last_name='Parent', email='parent@example.com', phone='555-0100', is_active=True)
            parent.set_password('Password123!')

            student = Student(first_name='Sam', last_name='Student', date_of_birth=date(2019, 5, 1), grade_level='Pre-K')
            student.parents.append(parent)
            db.session.add_all([sa, acct_staff, prog_staff, teacher, parent, student])
            db.session.flush()

            account = StudentFinancialAccount(student_id=student.id)
            db.session.add(account)
            db.session.commit()

            self.student_id = student.id
            self.account_id = account.id
            self.parent_id = parent.id
            self.teacher_id = teacher.id
            self.tokens = {
                'superadmin': create_access_token(identity=sa.email, additional_claims={'id': sa.id, 'role': 'superadmin', 'name': sa.name}),
                'accounting': create_access_token(identity=acct_staff.email, additional_claims={'id': acct_staff.id, 'role': 'staff', 'name': acct_staff.name}),
                'programs': create_access_token(identity=prog_staff.email, additional_claims={'id': prog_staff.id, 'role': 'staff', 'name': prog_staff.name}),
                'teacher': create_access_token(identity=teacher.email, additional_claims={'id': teacher.id, 'role': 'teacher', 'name': teacher.name}),
                'parent': create_access_token(identity=parent.email, additional_claims={'id': parent.id, 'role': 'parent', 'name': parent.name}),
                'student': create_access_token(identity='sam@student.ela.org', additional_claims={'id': student.id, 'role': 'student', 'name': 'Sam Student'}),
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # 1. Teacher Login / Token Validation Bug Fix
    def test_01_teacher_token_works_without_immediate_logout(self):
        t_token = self.tokens['teacher']
        res_profile = self.client.get('/api/profile', headers=_auth(t_token))
        self.assertEqual(res_profile.status_code, 200)
        self.assertEqual(res_profile.get_json()['role'], 'teacher')

        res_notifs = self.client.get('/api/notifications', headers=_auth(t_token))
        self.assertEqual(res_notifs.status_code, 200)

        res_time = self.client.get('/api/payroll/my/time-entries', headers=_auth(t_token))
        self.assertEqual(res_time.status_code, 200)

    # 2. RBAC Allow-List Enforcement
    def test_02_parent_blocked_from_all_admin_and_billing_routes(self):
        p_token = self.tokens['parent']
        for path in [
            '/api/billing/plans',
            '/api/subsidies',
            '/api/admissions/leads',
            '/api/enrollment/forms',
            '/api/students',
            '/api/staff',
            '/api/departments',
            '/api/payroll/admin/pay/preview',
        ]:
            res = self.client.get(path, headers=_auth(p_token))
            self.assertEqual(res.status_code, 403, f"Parent should get 403 on {path}, got {res.status_code}")

    def test_03_teacher_blocked_from_billing_admissions_but_reads_students(self):
        t_token = self.tokens['teacher']
        self.assertEqual(self.client.get('/api/billing/plans', headers=_auth(t_token)).status_code, 403)
        self.assertEqual(self.client.get('/api/admissions/leads', headers=_auth(t_token)).status_code, 403)
        self.assertEqual(self.client.get('/api/enrollment/forms', headers=_auth(t_token)).status_code, 403)
        self.assertEqual(self.client.get('/api/students', headers=_auth(t_token)).status_code, 200)

    def test_04_department_scoping_on_billing(self):
        self.assertEqual(self.client.get('/api/billing/plans', headers=_auth(self.tokens['programs'])).status_code, 403)
        self.assertEqual(self.client.get('/api/billing/plans', headers=_auth(self.tokens['accounting'])).status_code, 200)
        self.assertEqual(self.client.get('/api/billing/plans', headers=_auth(self.tokens['superadmin'])).status_code, 200)

    def test_05_future_student_role_blocked_by_default(self):
        s_token = self.tokens['student']
        for path in ['/api/billing/plans', '/api/students', '/api/staff', '/api/boards']:
            res = self.client.get(path, headers=_auth(s_token))
            self.assertIn(res.status_code, (401, 403))

    # 3. Private Static Files & Student Document Download Auth
    def test_06_sensitive_static_paths_require_auth(self):
        self.assertEqual(self.client.get('/api/static/documents/secret_immunization.pdf').status_code, 401)
        self.assertEqual(self.client.get('/api/static/reports/financials.pdf').status_code, 401)
        self.assertEqual(self.client.get('/api/static/uploads/messaging/private.jpg').status_code, 401)
        self.assertEqual(self.client.get('/api/students/documents/download/secret.pdf').status_code, 401)

    # 4. Socket.IO Authentication
    def test_07_socketio_rejects_unauthenticated_and_accepts_jwt(self):
        unauth_client = socketio.test_client(self.app)
        self.assertFalse(unauth_client.is_connected())

        auth_client = socketio.test_client(self.app, auth={'token': self.tokens['teacher']})
        self.assertTrue(auth_client.is_connected())
        auth_client.disconnect()

    # 5. OTP Brute-Force Lockout
    def test_08_otp_brute_force_invalidates_after_5_attempts(self):
        with self.app.app_context():
            otp_rec = LoginOTP(
                email='clara@ela.org',
                otp='123456',
                role='teacher',
                claims={'id': self.teacher_id, 'role': 'teacher', 'name': 'Clara Teacher'},
                expiry=datetime.utcnow() + timedelta(minutes=10),
            )
            db.session.add(otp_rec)
            db.session.commit()

        for _ in range(4):
            res = self.client.post('/api/auth/verify-login-otp', json={'email': 'clara@ela.org', 'otp': '000000'})
            self.assertEqual(res.status_code, 400)

        res_5th = self.client.post('/api/auth/verify-login-otp', json={'email': 'clara@ela.org', 'otp': '000000'})
        self.assertEqual(res_5th.status_code, 429)

        res_real = self.client.post('/api/auth/verify-login-otp', json={'email': 'clara@ela.org', 'otp': '123456'})
        self.assertEqual(res_real.status_code, 400)

    # 6. Stripe Webhook Atomicity & Retry on Failure
    def test_09_stripe_webhook_rolls_back_and_returns_500_on_error(self):
        fake_event = {
            'id': 'evt_test_atomic_1',
            'type': 'payment_intent.succeeded',
            'data': {'object': {'id': 'pi_test_1', 'amount_received': 10000, 'metadata': {'student_id': str(self.student_id)}}}
        }

        class FakeStripe:
            class error:
                class SignatureVerificationError(Exception):
                    pass
            class Webhook:
                @staticmethod
                def construct_event(payload, sig, secret):
                    return fake_event

        with patch('app.routes.stripe_webhook_routes.stripe', FakeStripe):
            with patch('app.routes.stripe_webhook_routes._handle_payment_intent_succeeded', side_effect=RuntimeError('DB blip')):
                res = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 't=1,v1=sig'})
                self.assertEqual(res.status_code, 500)

        with self.app.app_context():
            self.assertIsNone(ProcessedStripeEvent.query.filter_by(event_id='evt_test_atomic_1').first())

        with patch('app.routes.stripe_webhook_routes.stripe', FakeStripe):
            res_ok = self.client.post('/api/stripe/webhook', data=b'{}', headers={'Stripe-Signature': 't=1,v1=sig'})
            self.assertEqual(res_ok.status_code, 200)

        with self.app.app_context():
            self.assertIsNotNone(ProcessedStripeEvent.query.filter_by(event_id='evt_test_atomic_1').first())
            self.assertIsNotNone(Payment.query.filter_by(stripe_payment_intent_id='pi_test_1').first())

    # 7. Parent Balance Excludes Void & Draft Invoices
    def test_10_parent_balance_excludes_draft_and_void_invoices(self):
        with self.app.app_context():
            inv_sent = Invoice(account_id=self.account_id, status='Sent')
            inv_sent.items.append(InvoiceItem(description='Tuition', amount=500.0))
            inv_void = Invoice(account_id=self.account_id, status='Void')
            inv_void.items.append(InvoiceItem(description='Voided Fee', amount=300.0))
            inv_draft = Invoice(account_id=self.account_id, status='Draft')
            inv_draft.items.append(InvoiceItem(description='Draft Fee', amount=200.0))
            db.session.add_all([inv_sent, inv_void, inv_draft])
            db.session.commit()

        captured = {}

        def fake_create_pi(**kwargs):
            captured['amount_in_cents'] = kwargs['amount_in_cents']
            class FakeIntent:
                id = 'pi_balance_check'
                status = 'requires_payment_method'
                client_secret = 'pi_secret_123'
            return FakeIntent()

        with patch('app.services.stripe_service.get_or_create_stripe_customer', return_value='cus_test'), \
             patch('app.services.stripe_service.create_payment_intent', side_effect=fake_create_pi):
            res = self.client.post(
                '/api/parent/create-payment-intent',
                json={'student_id': self.student_id},
                headers=_auth(self.tokens['parent']),
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(captured['amount_in_cents'], 50000)

    # 8. Payroll Duplicate Run Protection & No Fake Defaults
    def test_11_payroll_duplicate_run_blocked_and_no_fake_defaults(self):
        with self.app.app_context():
            new_staff = Staff(name='New Hire', email='newhire@ela.org')
            new_staff.set_password('Password123!')
            db.session.add(new_staff)
            db.session.commit()
            d = new_staff.to_dict()
            self.assertEqual(d['ssn_last4'], '')
            self.assertEqual(d['bank_routing'], '')
            self.assertEqual(d['bank_name'], '')
            self.assertEqual(d['manager_name'], '')

        payload = {
            'run_type': 'us_contractor',
            'pay_period_start': '2026-11-01',
            'pay_period_end': '2026-11-15',
            'items': [{'staff_id': self.teacher_id, 'hours_worked': 10, 'hourly_rate': 25}],
        }
        res1 = self.client.post('/api/payroll/admin/pay/run', json=payload, headers=_auth(self.tokens['superadmin']))
        self.assertEqual(res1.status_code, 201)
        res2 = self.client.post('/api/payroll/admin/pay/run', json=payload, headers=_auth(self.tokens['superadmin']))
        self.assertEqual(res2.status_code, 409)


if __name__ == '__main__':
    unittest.main()
