from datetime import date, time, datetime, timedelta
import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (
    DailyAttendance,
    Holiday,
    UserProfile,
    EmployeeFaceProfile,
    Department,
    LeaveRequest,
    AttendanceAuditLog
)
from .attendance_logic import (
    OFFICE_START_TIME,
    ON_TIME_END_TIME,
    OFFICE_CLOSING_TIME,
    calculate_late_minutes,
    calculate_working_duration,
    evaluate_attendance_row,
    get_monthly_attendance_data,
    get_admin_dashboard_kpis
)


class AttendanceSystemTests(TestCase):
    """
    Comprehensive test suite covering all 15 critical workflows specified by requirements:
    1. Admin Login Redirect
    2. Employee Login Redirect
    3. Access to Employee Dashboard
    4. Face Verification on Punch In/Out
    5. On-Time Punch In (9:30 - 9:35 AM Green)
    6. Late Punch In (>9:35 AM Red)
    7. Closed Day without Punch (Yellow)
    8. Punch Out Pending (Teal)
    9. Sundays and Company Holidays (Blue)
    10. Apply for Leave (Pending Status & Overlap Prevention)
    11. Approved Leaves (Purple in attendance)
    12. Admin Approval & Remarks on Leave
    13. Access Denied to Admin Pages for Employees
    14. Isolation of Employee Data
    15. Logout & Database Integrity
    """

    def setUp(self):
        # Create department
        self.dept = Department.objects.create(name="Engineering")

        # Create normal employee
        self.employee = User.objects.create_user(
            username="testemployee",
            password="Password123!",
            first_name="Test",
            last_name="Employee",
            email="employee@example.com"
        )
        self.emp_profile = UserProfile.objects.get(user=self.employee)
        self.emp_profile.department = self.dept
        self.emp_profile.employee_id = "EMP0001"
        self.emp_profile.save()

        # Enroll biometric 128-d face descriptor vector for test employee
        self.emp_face = EmployeeFaceProfile.objects.get(user=self.employee)
        self.sample_descriptor = [0.1] * 128
        self.emp_face.enrollment_status = 'REGISTERED'
        self.emp_face.face_descriptor = self.sample_descriptor
        self.emp_face.sample_count = 4
        self.emp_face.save()

        # Create another normal employee for data isolation testing
        self.other_employee = User.objects.create_user(
            username="otheremployee",
            password="Password123!",
            first_name="Other",
            last_name="User"
        )
        self.other_profile = UserProfile.objects.get(user=self.other_employee)
        self.other_profile.employee_id = "EMP0002"
        self.other_profile.save()

        # Create admin user
        self.admin = User.objects.create_superuser(
            username="adminuser",
            password="AdminPassword123!",
            email="admin@example.com"
        )

        self.client = Client()

    # -----------------------------------------------------
    # Test 1: Admin Login Redirect (Admin Dashboard)
    # -----------------------------------------------------
    def test_01_admin_login_redirects_to_admin_dashboard(self):
        response = self.client.post("/loginform/", {
            "username": "adminuser",
            "password": "AdminPassword123!",
            "login_role": "admin"
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/admin-dashboard/")

    # -----------------------------------------------------
    # Test 2: Employee Login Redirect (Employee Dashboard)
    # -----------------------------------------------------
    def test_02_employee_login_redirects_to_employee_dashboard(self):
        response = self.client.post("/loginform/", {
            "username": "testemployee",
            "password": "Password123!",
            "login_role": "employee"
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/employee/dashboard/")

    # -----------------------------------------------------
    # Test 3: Access to Employee Dashboard (View attendance status & shift)
    # -----------------------------------------------------
    def test_03_access_employee_dashboard(self):
        self.client.login(username="testemployee", password="Password123!")
        response = self.client.get("/employee/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("profile", response.context)
        self.assertIn("today_eval", response.context)
        self.assertIn("month_summary", response.context)
        self.assertEqual(response.context["office_start_time"], "09:30 AM")
        self.assertEqual(response.context["office_closing_time"], "06:30 PM")

    # -----------------------------------------------------
    # Test 4: Face Verification on Punch In/Out (Server-validated AI Biometrics)
    # -----------------------------------------------------
    def test_04_face_verification_punch_in_and_out(self):
        self.client.login(username="testemployee", password="Password123!")
        today = timezone.localtime(timezone.now()).date()

        # Step A: Attempt with matching candidate face descriptor (Distance = 0.0 <= 0.55)
        response_in = self.client.post("/punch/", {
            "action": "punch_in",
            "face_descriptor": json.dumps(self.sample_descriptor),
            "liveness_score": "1.0"
        })
        self.assertEqual(response_in.status_code, 200)
        self.assertEqual(response_in.json()["status"], "punchin")

        # Step B: Attempt Punch Out with same face
        response_out = self.client.post("/punch/", {
            "action": "punch_out",
            "face_descriptor": json.dumps(self.sample_descriptor),
            "confirm_early": "true"
        })
        self.assertEqual(response_out.status_code, 200)
        self.assertEqual(response_out.json()["status"], "punchout")

        # Step C: Face Mismatch test with distant vector ([0.9] * 128)
        mismatch_descriptor = [0.9] * 128
        response_mismatch = self.client.post("/punch/", {
            "action": "punch_in",
            "face_descriptor": json.dumps(mismatch_descriptor)
        })
        self.assertEqual(response_mismatch.status_code, 400)
        self.assertEqual(response_mismatch.json()["code"], "FACE_MISMATCH")

    # -----------------------------------------------------
    # Test 5: On-time punch in (9:30 AM to 9:35 AM marked as Green)
    # -----------------------------------------------------
    def test_05_on_time_punch_in_green(self):
        d1 = date(2026, 9, 1)  # Tuesday 9:30 AM
        rec1 = DailyAttendance.objects.create(
            user=self.employee,
            date=d1,
            punch_in=time(9, 30, 0),
            punch_out=time(18, 30, 0)
        )
        self.assertFalse(rec1.is_late)
        self.assertEqual(rec1.late_minutes, 0)
        eval1 = evaluate_attendance_row(self.employee, d1, record=rec1)
        self.assertEqual(eval1["status"], "Present / On Time")
        self.assertEqual(eval1["status_color"], "green")
        self.assertEqual(eval1["row_class"], "row-present")

        d2 = date(2026, 9, 2)  # Wednesday 9:35 AM (end of grace window)
        rec2 = DailyAttendance.objects.create(
            user=self.employee,
            date=d2,
            punch_in=time(9, 35, 0),
            punch_out=time(18, 30, 0)
        )
        self.assertFalse(rec2.is_late)
        self.assertEqual(rec2.late_minutes, 0)
        eval2 = evaluate_attendance_row(self.employee, d2, record=rec2)
        self.assertEqual(eval2["status"], "Present / On Time")
        self.assertEqual(eval2["status_color"], "green")

    # -----------------------------------------------------
    # Test 6: Late punch in (>9:35 AM marked as Red with delay minutes)
    # -----------------------------------------------------
    def test_06_late_punch_in_red_with_delay(self):
        d = date(2026, 9, 3)  # Thursday 9:45 AM
        rec = DailyAttendance.objects.create(
            user=self.employee,
            date=d,
            punch_in=time(9, 45, 0),
            punch_out=time(18, 30, 0)
        )
        self.assertTrue(rec.is_late)
        # Delay calculated relative to office start 9:30 AM -> 15 minutes
        self.assertEqual(rec.late_minutes, 15)
        eval_row = evaluate_attendance_row(self.employee, d, record=rec)
        self.assertEqual(eval_row["status"], "Late")
        self.assertEqual(eval_row["status_color"], "red")
        self.assertEqual(eval_row["row_class"], "row-late")
        self.assertIn("15 mins late", eval_row["delay"])

    # -----------------------------------------------------
    # Test 7: Closed day without punch (Yellow)
    # -----------------------------------------------------
    def test_07_closed_day_without_punch_yellow(self):
        past_date = date(2026, 9, 1)  # Tuesday in past with no punch
        eval_row = evaluate_attendance_row(self.employee, past_date, record=None)
        self.assertEqual(eval_row["status"], "Absent")
        self.assertEqual(eval_row["status_color"], "yellow")
        self.assertEqual(eval_row["row_class"], "row-absent")

    # -----------------------------------------------------
    # Test 8: Punch Out Pending (Teal)
    # -----------------------------------------------------
    def test_08_punch_out_pending_teal(self):
        d = date(2026, 9, 8)  # Tuesday
        rec = DailyAttendance.objects.create(
            user=self.employee,
            date=d,
            punch_in=time(9, 30, 0),
            punch_out=None
        )
        eval_row = evaluate_attendance_row(self.employee, d, record=rec)
        self.assertEqual(eval_row["status"], "Punch Out Pending")
        self.assertEqual(eval_row["status_color"], "teal")
        self.assertEqual(eval_row["row_class"], "row-pending")

    # -----------------------------------------------------
    # Test 9: Sundays and Company Holidays (Blue)
    # -----------------------------------------------------
    def test_09_sundays_and_holidays_blue(self):
        # Sunday (6 Sep 2026)
        sunday = date(2026, 9, 6)
        eval_sun = evaluate_attendance_row(self.employee, sunday)
        self.assertEqual(eval_sun["status"], "Holiday")
        self.assertEqual(eval_sun["status_color"], "blue")
        self.assertEqual(eval_sun["row_class"], "row-holiday")

        # Company Holiday on a weekday (15 Sep 2026)
        Holiday.objects.create(name="National Holiday", date=date(2026, 9, 15))
        eval_hol = evaluate_attendance_row(self.employee, date(2026, 9, 15))
        self.assertEqual(eval_hol["status"], "Holiday")
        self.assertEqual(eval_hol["status_color"], "blue")
        self.assertEqual(eval_hol["row_class"], "row-holiday")

    # -----------------------------------------------------
    # Test 10: Apply for Leave (Pending status, overlapping prevention)
    # -----------------------------------------------------
    def test_10_apply_for_leave_pending_and_overlap_prevention(self):
        self.client.login(username="testemployee", password="Password123!")

        # Apply for 3 days of Casual Leave
        response = self.client.post("/employee/leave/apply/", {
            "leave_type": "Casual Leave",
            "from_date": "2026-10-10",
            "to_date": "2026-10-12",
            "reason": "Family function"
        })
        self.assertEqual(response.status_code, 302)

        # Check saved record
        leave = LeaveRequest.objects.filter(user=self.employee, from_date="2026-10-10").first()
        self.assertIsNotNone(leave)
        self.assertEqual(leave.status, "Pending")
        self.assertEqual(leave.total_days, 3)

        # Attempt to apply overlapping leave request (should be rejected)
        response_overlap = self.client.post("/employee/leave/apply/", {
            "leave_type": "Sick Leave",
            "from_date": "2026-10-11",
            "to_date": "2026-10-13",
            "reason": "Overlapping attempt"
        })
        # Should render form with error message (status 200, not redirect)
        self.assertEqual(response_overlap.status_code, 200)
        self.assertEqual(LeaveRequest.objects.filter(user=self.employee).count(), 1)

    # -----------------------------------------------------
    # Test 11: Approved Leaves (Purple, updates attendance)
    # -----------------------------------------------------
    def test_11_approved_leaves_purple(self):
        leave_date = date(2026, 9, 18)  # Friday
        LeaveRequest.objects.create(
            user=self.employee,
            leave_type="Paid Leave",
            from_date=leave_date,
            to_date=leave_date,
            total_days=1,
            reason="Vacation",
            status="Approved"
        )
        eval_leave = evaluate_attendance_row(self.employee, leave_date)
        self.assertIn("Leave", eval_leave["status"])
        self.assertEqual(eval_leave["status_color"], "purple")
        self.assertEqual(eval_leave["row_class"], "row-leave")

    # -----------------------------------------------------
    # Test 12: Admin Approval & Remarks on Leave
    # -----------------------------------------------------
    def test_12_admin_leave_approval_with_remarks(self):
        leave = LeaveRequest.objects.create(
            user=self.employee,
            leave_type="Sick Leave",
            from_date=date(2026, 11, 1),
            to_date=date(2026, 11, 2),
            total_days=2,
            reason="Medical recovery",
            status="Pending"
        )

        self.client.login(username="adminuser", password="AdminPassword123!")
        response = self.client.post(f"/admin-dashboard/leave/action/{leave.id}/", {
            "action": "Approved",
            "admin_remark": "Approved by HR Director. Get well soon!"
        })
        self.assertEqual(response.status_code, 302)

        leave.refresh_from_db()
        self.assertEqual(leave.status, "Approved")
        self.assertEqual(leave.reviewed_by, self.admin)
        self.assertEqual(leave.admin_remark, "Approved by HR Director. Get well soon!")

    # -----------------------------------------------------
    # Test 13: Access Denied to Admin Pages for Employees (Redirect to Employee Dashboard)
    # -----------------------------------------------------
    def test_13_employee_access_admin_dashboard_denied(self):
        self.client.login(username="testemployee", password="Password123!")
        response = self.client.get("/admin-dashboard/")
        # Must be redirected to employee dashboard with access denied error
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/employee/dashboard/")

        # Test another admin endpoint
        response2 = self.client.get("/admin-dashboard/employees/")
        self.assertEqual(response2.status_code, 302)
        self.assertEqual(response2.url, "/employee/dashboard/")

    # -----------------------------------------------------
    # Test 14: Isolation of Employee Data (Employee only sees own data)
    # -----------------------------------------------------
    def test_14_isolation_of_employee_data(self):
        self.client.login(username="testemployee", password="Password123!")

        # Attempt to request other employee's attendance
        response = self.client.get(f"/employee/attendance/?employee_id={self.other_employee.id}")
        self.assertEqual(response.status_code, 200)
        # Normal employees should only see their own logged in user data
        self.assertEqual(response.context["user"], self.employee)

        # Attempt to cancel other employee's leave request
        other_leave = LeaveRequest.objects.create(
            user=self.other_employee,
            leave_type="Casual Leave",
            from_date=date(2026, 12, 1),
            to_date=date(2026, 12, 1),
            total_days=1,
            reason="Personal",
            status="Pending"
        )
        response_cancel = self.client.post(f"/employee/leave/cancel/{other_leave.id}/")
        # get_object_or_404 with user=request.user returns 404
        self.assertEqual(response_cancel.status_code, 404)
        other_leave.refresh_from_db()
        self.assertEqual(other_leave.status, "Pending")

    # -----------------------------------------------------
    # Test 15: Logout & Database Integrity (Existing data intact)
    # -----------------------------------------------------
    def test_15_logout_and_database_integrity(self):
        self.client.login(username="testemployee", password="Password123!")
        response = self.client.get("/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/loginn/")

        # Ensure session is terminated
        response_dash = self.client.get("/employee/dashboard/")
        self.assertEqual(response_dash.status_code, 302)
        self.assertIn("/loginn/", response_dash.url)

        # Verify database records persist cleanly
        self.assertGreaterEqual(User.objects.count(), 3)
        self.assertGreaterEqual(UserProfile.objects.count(), 2)
        self.assertGreaterEqual(EmployeeFaceProfile.objects.count(), 2)
