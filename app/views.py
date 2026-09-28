from datetime import datetime, date, time, timedelta
import json
import logging
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    DailyAttendance,
    Department,
    UserProfile,
    EmployeeFaceProfile,
    FaceChallenge,
    FaceVerificationLog,
    Holiday,
    LeaveRequest,
    AttendanceCorrectionRequest,
    AttendanceAuditLog
)
from .attendance_logic import (
    OFFICE_START_TIME,
    ON_TIME_END_TIME,
    OFFICE_CLOSING_TIME,
    SHIFT_DURATION_HOURS,
    get_current_kolkata_datetime,
    is_sunday,
    get_holiday_for_date,
    calculate_late_minutes,
    calculate_working_duration,
    evaluate_attendance_row,
    get_monthly_attendance_data,
    get_admin_dashboard_kpis,
    generate_attendance_csv,
    generate_attendance_excel,
    get_client_ip,
    compute_centroid_descriptor,
)

logger = logging.getLogger(__name__)


# =========================================================
# PERMISSION DECORATORS & ROLE REDIRECTION
# =========================================================

def admin_required(view_func):
    """
    Ensures user is authenticated and has administrative privileges (staff or superuser).
    Employees attempting to access admin URLs are blocked and redirected to Employee Dashboard.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(
                request,
                "⚠️ Access Denied: You do not have permission to access the Administrator Portal."
            )
            return redirect("employee_dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


def employee_required(view_func):
    """
    Ensures user is authenticated.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return view_func(request, *args, **kwargs)
    return _wrapped


# =========================================================
# AUTHENTICATION & LOGIN VIEWS
# =========================================================

def home(request):
    """Root redirect based on authenticated user role."""
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect("admin_dashboard")
        return redirect("employee_dashboard")
    return redirect("loginn")


def loginn(request):
    """Display modern login page with separate Employee and Admin tabs."""
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect("admin_dashboard")
        return redirect("employee_dashboard")
    role_hint = request.GET.get("role", "employee")
    return render(request, "loginn.html", {"role_hint": role_hint})


def loginform(request):
    """
    Role-based authentication:
    - Admin Login -> Redirects to Admin Dashboard
    - Employee Login -> Redirects to Employee Dashboard
    """
    if request.method != "POST":
        return redirect("loginn")

    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")
    login_role = request.POST.get("login_role", "employee").strip().lower()

    if not username or not password:
        messages.error(request, "Please enter both username and password.")
        return render(request, "loginn.html", {"role_hint": login_role})

    user = authenticate(request, username=username, password=password)

    if user is not None:
        if not user.is_active:
            messages.error(request, "❌ Your account is currently inactive. Contact your administrator.")
            return render(request, "loginn.html", {"role_hint": login_role})

        # Role validation
        if login_role == "admin" and not (user.is_staff or user.is_superuser):
            messages.error(
                request,
                "❌ Access Denied: This account does not have administrative privileges. Please use the Employee Login tab."
            )
            return render(request, "loginn.html", {"role_hint": "admin"})

        login(request, user)
        # Ensure profiles exist
        UserProfile.objects.get_or_create(user=user)
        EmployeeFaceProfile.objects.get_or_create(user=user)

        messages.success(request, f"Welcome back, {user.first_name or user.username}!")

        # Role-based redirection
        if user.is_staff or user.is_superuser:
            if login_role == "employee":
                # Admin chose employee view
                return redirect("employee_dashboard")
            return redirect("admin_dashboard")
        else:
            return redirect("employee_dashboard")

    messages.error(request, "❌ Invalid username or password. Please try again.")
    return render(request, "loginn.html", {"role_hint": login_role})


def user_logout(request):
    """Logout current user and return to login screen."""
    logout(request)
    messages.info(request, "You have been logged out successfully.")
    return redirect("loginn")


def registr(request):
    """Display registration page with departments."""
    departments = Department.objects.all()
    return render(request, "registr.html", {"departments": departments})


def registeruser(request):
    """Create a new employee user account."""
    if request.method != "POST":
        return redirect("registr")

    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    department_id = request.POST.get("department")
    designation = request.POST.get("designation", "Employee").strip()
    phone = request.POST.get("phone", "").strip()

    if not username or not password:
        messages.error(request, "⚠️ Username and password are required.")
        return render(request, "registr.html", {"departments": Department.objects.all()})

    if User.objects.filter(username=username).exists():
        messages.error(request, "⚠️ Username is already taken. Please choose another.")
        return render(request, "registr.html", {"departments": Department.objects.all()})

    try:
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name
        )

        dept = Department.objects.filter(id=department_id).first() if department_id else None
        emp_id = f"EMP{user.id:04d}"

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.employee_id = emp_id
        profile.department = dept
        profile.designation = designation or "Employee"
        profile.phone = phone
        profile.save()

        EmployeeFaceProfile.objects.get_or_create(user=user)

        messages.success(request, "✅ Registration successful! Please login with your credentials.")
        return redirect("loginn")
    except Exception as e:
        logger.error(f"Error registering user: {e}")
        messages.error(request, f"Registration error: {e}")
        return render(request, "registr.html", {"departments": Department.objects.all()})


# =========================================================
# EMPLOYEE DASHBOARD & DEDICATED PAGES
# =========================================================

@login_required
@employee_required
def employee_dashboard(request):
    """
    Main Employee Dashboard:
    - Employee Name, Employee ID, Profile Picture, Department
    - Current Date & Live Clock
    - Shift Timing: 9:30 AM – 6:30 PM (9.0 Hours)
    - Today's Attendance Status, Punch In, Punch Out, Total Working Hours
    - Monthly Present Days, Late Days, Absent Days, Approved Leaves, Pending Leaves
    - Quick Face Punch trigger
    """
    user = request.user
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    profile, _ = UserProfile.objects.get_or_create(user=user)
    face_profile, _ = EmployeeFaceProfile.objects.get_or_create(user=user)

    today_record = DailyAttendance.objects.filter(user=user, date=today).first()
    today_eval = evaluate_attendance_row(user=user, date_obj=today, record=today_record)

    month_data = get_monthly_attendance_data(user, today.year, today.month)

    # Approved & Pending leave counts
    approved_leaves_count = LeaveRequest.objects.filter(
        user=user,
        status='Approved',
        from_date__year=today.year,
        from_date__month=today.month
    ).count()

    pending_leaves_count = LeaveRequest.objects.filter(
        user=user,
        status='Pending'
    ).count()

    # Recent 7 attendance records
    recent_records = DailyAttendance.objects.filter(user=user).order_by("-date")[:7]

    context = {
        "user": user,
        "profile": profile,
        "face_profile": face_profile,
        "today_record": today_record,
        "today_eval": today_eval,
        "today_date": today,
        "current_time": now_dt.time(),
        "month_summary": month_data["summary"],
        "approved_leaves_count": approved_leaves_count,
        "pending_leaves_count": pending_leaves_count,
        "recent_records": recent_records,
        "office_start_time": OFFICE_START_TIME.strftime("%I:%M %p"),
        "on_time_end_time": ON_TIME_END_TIME.strftime("%I:%M %p"),
        "office_closing_time": OFFICE_CLOSING_TIME.strftime("%I:%M %p"),
        "shift_duration": "9.0 Hours",
    }

    return render(request, "employee_dashboard.html", context)


@login_required
@employee_required
def employee_attendance(request):
    """
    Separate 'My Attendance' Page:
    - View daily and monthly attendance matrix
    - Punch In / Out times, total hours, delay in minutes
    - Filter by date, month, year
    - Colour-coded rows (Green, Red, Yellow, Teal, Blue, Purple)
    - Download own attendance report in Excel (.xlsx) and CSV
    """
    user = request.user
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    try:
        month = int(request.GET.get("month", today.month))
        year = int(request.GET.get("year", today.year))
    except (ValueError, TypeError):
        month, year = today.month, today.year

    if month < 1 or month > 12:
        month = today.month
    if year < 2020 or year > 2040:
        year = today.year

    specific_date_str = request.GET.get("date", "").strip()
    specific_record = None
    if specific_date_str:
        try:
            s_date = datetime.strptime(specific_date_str, "%Y-%m-%d").date()
            specific_record = DailyAttendance.objects.filter(user=user, date=s_date).first()
        except ValueError:
            pass

    month_data = get_monthly_attendance_data(user, year, month)

    month_names = [(i, datetime(2000, i, 1).strftime("%B")) for i in range(1, 13)]
    years_list = range(today.year - 2, today.year + 2)

    context = {
        "user": user,
        "year": year,
        "month": month,
        "month_name": datetime(2000, month, 1).strftime("%B"),
        "month_names": month_names,
        "years_list": years_list,
        "days": month_data["days"],
        "summary": month_data["summary"],
        "specific_date": specific_date_str,
        "specific_record": specific_record,
    }

    return render(request, "employee_attendance.html", context)


@login_required
@employee_required
def employee_scanner(request):
    """
    Dedicated Face Scanner & Attendance Punch Page:
    - Live camera preview with facial landmark reticle
    - Single-face check, blink / movement anti-spoofing
    - One-time challenge token validation
    - Punch In and Punch Out with server timestamp
    """
    user = request.user
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    profile, _ = UserProfile.objects.get_or_create(user=user)
    face_profile, _ = EmployeeFaceProfile.objects.get_or_create(user=user)

    today_record = DailyAttendance.objects.filter(user=user, date=today).first()
    today_eval = evaluate_attendance_row(user=user, date_obj=today, record=today_record)

    context = {
        "user": user,
        "profile": profile,
        "face_profile": face_profile,
        "today_record": today_record,
        "today_eval": today_eval,
        "today_date": today,
        "office_start_time": OFFICE_START_TIME.strftime("%I:%M %p"),
        "on_time_end_time": ON_TIME_END_TIME.strftime("%I:%M %p"),
        "office_closing_time": OFFICE_CLOSING_TIME.strftime("%I:%M %p"),
    }

    return render(request, "employee_scanner.html", context)


@login_required
@employee_required
def employee_apply_leave(request):
    """
    Separate 'Apply for Leave' Page:
    - Form: Leave Type, Start Date, End Date, Number of Days, Reason, Supporting Document
    - Validations: date range validity, no overlapping existing requests
    - Saves with status='Pending'
    """
    user = request.user
    today = get_current_kolkata_datetime().date()

    if request.method == "POST":
        leave_type = request.POST.get("leave_type", "").strip()
        from_date_str = request.POST.get("from_date", "").strip()
        to_date_str = request.POST.get("to_date", "").strip()
        reason = request.POST.get("reason", "").strip()
        supporting_doc = request.FILES.get("supporting_document")

        if not leave_type or not from_date_str or not to_date_str or not reason:
            messages.error(request, "⚠️ Please fill all required fields.")
            return render(request, "employee_apply_leave.html", {"today_date": today})

        try:
            from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
            to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()

            if to_date < from_date:
                messages.error(request, "⚠️ 'End Date' cannot be earlier than 'Start Date'.")
                return render(request, "employee_apply_leave.html", {"today_date": today})

            # Check overlapping existing approved or pending leave requests
            overlapping = LeaveRequest.objects.filter(
                user=user,
                status__in=['Pending', 'Approved'],
                is_cancelled=False,
                from_date__lte=to_date,
                to_date__gte=from_date
            ).exists()

            if overlapping:
                messages.error(request, "⚠️ You already have an active/pending leave request covering these dates.")
                return render(request, "employee_apply_leave.html", {"today_date": today})

            total_days = (to_date - from_date).days + 1

            LeaveRequest.objects.create(
                user=user,
                leave_type=leave_type,
                from_date=from_date,
                to_date=to_date,
                total_days=total_days,
                reason=reason,
                supporting_document=supporting_doc,
                status="Pending"
            )

            messages.success(request, f"✅ Leave request submitted successfully ({total_days} days). Initial status: Pending.")
            return redirect("employee_leave_requests")
        except Exception as e:
            logger.error(f"Error submitting leave: {e}")
            messages.error(request, f"Submission error: {e}")

    return render(request, "employee_apply_leave.html", {"today_date": today})


@login_required
@employee_required
def employee_leave_requests(request):
    """
    Separate 'My Leave Requests' Page:
    - Lists all submitted applications
    - Badges for Pending, Approved, Rejected, Cancelled
    - Displays admin remarks / rejection reason
    - Cancel button for Pending requests
    """
    user = request.user
    leaves = LeaveRequest.objects.filter(user=user).order_by("-created_at")

    return render(request, "employee_leave_requests.html", {"leaves": leaves})


@login_required
@employee_required
@require_http_methods(["POST"])
def employee_cancel_leave(request, leave_id):
    """Cancel a pending leave request."""
    leave = get_object_or_404(LeaveRequest, id=leave_id, user=request.user)

    if leave.status != "Pending":
        messages.error(request, "⚠️ Only pending leave requests can be cancelled.")
        return redirect("employee_leave_requests")

    leave.status = "Cancelled"
    leave.is_cancelled = True
    leave.save()

    messages.success(request, "✅ Leave request has been cancelled.")
    return redirect("employee_leave_requests")


@login_required
@employee_required
def employee_profile(request):
    """
    Separate 'My Profile' Page:
    - View Name, Employee ID, Profile Picture, Department, Designation, Phone, Email, Joining Date
    - Update profile picture and phone
    - Biometrics Enrollment Status
    """
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)
    face_profile, _ = EmployeeFaceProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_profile":
            phone = request.POST.get("phone", "").strip()
            profile.phone = phone

            if "profile_picture" in request.FILES:
                profile.profile_picture = request.FILES["profile_picture"]

            profile.save()
            messages.success(request, "✅ Profile updated successfully.")
            return redirect("employee_profile")

        elif action == "update_password":
            old_p = request.POST.get("old_password", "")
            new_p = request.POST.get("new_password", "")
            conf_p = request.POST.get("confirm_password", "")

            if not user.check_password(old_p):
                messages.error(request, "❌ Current password is incorrect.")
            elif new_p != conf_p:
                messages.error(request, "❌ New passwords do not match.")
            elif len(new_p) < 6:
                messages.error(request, "❌ New password must be at least 6 characters.")
            else:
                user.set_password(new_p)
                user.save()
                messages.success(request, "✅ Password updated successfully! Please login with your new password.")
                return redirect("loginn")

    return render(request, "employee_profile.html", {
        "user": user,
        "profile": profile,
        "face_profile": face_profile,
    })


@login_required
@employee_required
def employee_export_csv(request):
    """Export employee's own monthly attendance report as CSV."""
    user = request.user
    today = get_current_kolkata_datetime().date()

    month = int(request.GET.get("month", today.month))
    year = int(request.GET.get("year", today.year))

    m_data = get_monthly_attendance_data(user, year, month)
    flattened_rows = []
    for day_row in m_data["days"]:
        flattened_rows.append({
            "employee_name": f"{user.first_name} {user.last_name}".strip() or user.username,
            "username": user.username,
            "date": day_row["date"],
            "day": day_row["day"],
            "punch_in": day_row["punch_in_formatted"],
            "punch_out": day_row["punch_out_formatted"],
            "working_hours": day_row["working_hours"],
            "status": day_row["status"],
            "delay": day_row["delay"],
            "location": day_row["location"],
            "verification_method": day_row["verification_method"],
        })

    csv_data = generate_attendance_csv(flattened_rows)
    response = HttpResponse(csv_data, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="my_attendance_{year}_{month:02d}.csv"'
    return response


@login_required
@employee_required
def employee_export_excel(request):
    """Export employee's own monthly attendance report as Excel."""
    user = request.user
    today = get_current_kolkata_datetime().date()

    month = int(request.GET.get("month", today.month))
    year = int(request.GET.get("year", today.year))

    m_data = get_monthly_attendance_data(user, year, month)
    flattened_rows = []
    for day_row in m_data["days"]:
        flattened_rows.append({
            "employee_name": f"{user.first_name} {user.last_name}".strip() or user.username,
            "username": user.username,
            "date": day_row["date"],
            "day": day_row["day"],
            "punch_in": day_row["punch_in_formatted"],
            "punch_out": day_row["punch_out_formatted"],
            "working_hours": day_row["working_hours"],
            "status": day_row["status"],
            "delay": day_row["delay"],
            "location": day_row["location"],
            "verification_method": day_row["verification_method"],
        })

    month_str = datetime(2000, month, 1).strftime("%B")
    excel_data = generate_attendance_excel(flattened_rows, title=f"My Attendance - {month_str} {year}")
    response = HttpResponse(
        excel_data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="my_attendance_{year}_{month:02d}.xlsx"'
    return response


# =========================================================
# BIOMETRIC FACE VERIFICATION & PUNCH APIS
# =========================================================

@login_required
def api_face_challenge(request):
    """
    Issues a one-time cryptographic challenge token to the client.
    Guarantees replay attack protection.
    """
    action = request.GET.get("action", "punch")
    challenge = FaceChallenge.create_challenge(user=request.user, action=action)
    return JsonResponse({
        "challenge_id": challenge.challenge_id,
        "user_id": request.user.id,
        "expires_in_seconds": 120
    })


@login_required
def api_face_status(request):
    """Returns biometric enrollment status and exemption flags."""
    target_user = request.user
    emp_id = request.GET.get("user_id")
    if emp_id and (request.user.is_staff or request.user.is_superuser):
        target_user = get_object_or_404(User, id=emp_id)

    profile, _ = UserProfile.objects.get_or_create(user=target_user)
    face_profile, _ = EmployeeFaceProfile.objects.get_or_create(user=target_user)

    return JsonResponse({
        "user_id": target_user.id,
        "username": target_user.username,
        "enrollment_status": face_profile.enrollment_status,
        "enrollment_status_display": face_profile.get_enrollment_status_display(),
        "is_enrolled": face_profile.is_enrolled,
        "sample_count": face_profile.sample_count,
        "enrolled_at": face_profile.enrolled_at.strftime("%Y-%m-%d %H:%M") if face_profile.enrolled_at else None,
        "face_auth_exempt": profile.face_auth_exempt,
        "exemption_reason": profile.exemption_reason,
    })


@login_required
@require_http_methods(["POST"])
def punch(request):
    """
    Atomic Punch In and Punch Out handler with:
    - Genuine biometric Euclidean matching
    - Anti-replay cryptographic challenge consumption
    - Single-face check
    - Audit verification logging
    - Server timestamp in Asia/Kolkata
    """
    user = request.user
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()
    current_time = now_dt.time()
    client_ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:250]

    action = request.POST.get("action", "").strip().lower()  # 'punch_in' or 'punch_out'
    latitude = request.POST.get("latitude", "").strip() or None
    longitude = request.POST.get("longitude", "").strip() or None
    location = request.POST.get("location", "").strip() or "Location unavailable"
    descriptor_raw = request.POST.get("face_descriptor", "")
    challenge_token = request.POST.get("challenge_id", "").strip()
    confirm_early = request.POST.get("confirm_early", "false").lower() == "true"

    profile, _ = UserProfile.objects.get_or_create(user=user)
    face_profile, _ = EmployeeFaceProfile.objects.get_or_create(user=user)

    punch_method = request.POST.get("method", "").strip().lower()
    is_gps_punch = (punch_method == "gps") or (not descriptor_raw and (latitude is not None or request.POST.get("use_gps") == "true"))

    is_exempt = profile.face_auth_exempt or is_gps_punch
    verification_method = "gps_location" if is_gps_punch else ("exempt_override" if profile.face_auth_exempt else "face_biometric")

    # 1. Anti-Replay Check (if challenge token is supplied)
    if challenge_token:
        challenge = FaceChallenge.objects.filter(challenge_id=challenge_token, user=user).first()
        if not challenge or not challenge.is_valid():
            FaceVerificationLog.objects.create(
                user=user,
                action=f"Punch ({action})",
                status="Replay Blocked",
                ip_address=client_ip,
                user_agent=user_agent,
                message="Challenge token expired or already consumed (Replay blocked)."
            )
            return JsonResponse({
                "status": "error",
                "code": "REPLAY_BLOCKED",
                "message": "⚠️ Replay attack blocked: One-time verification token expired or reused. Please refresh and try again."
            }, status=400)

        # Consume token immediately
        challenge.is_consumed = True
        challenge.consumed_at = timezone.now()
        challenge.save()

    # 2. Biometric Verification (Only when face descriptor is explicitly supplied or required)
    candidate_descriptor = None
    if not is_exempt:
        if not face_profile.is_enrolled or not face_profile.face_descriptor:
            FaceVerificationLog.objects.create(
                user=user,
                action=f"Punch ({action})",
                status="Not Registered",
                ip_address=client_ip,
                user_agent=user_agent,
                message="Attempted punch without registered face profile."
            )
            return JsonResponse({
                "status": "error",
                "code": "FACE_NOT_ENROLLED",
                "message": (
                    "⚠️ Biometric Face Not Registered! Face scanning is mandatory. "
                    "Please register your face profile before marking attendance."
                )
            }, status=400)

        if not descriptor_raw:
            return JsonResponse({
                "status": "error",
                "code": "NO_FACE_DATA",
                "message": "⚠️ No face scan data received. Camera scan is required."
            }, status=400)

        try:
            candidate_descriptor = json.loads(descriptor_raw)
            if not isinstance(candidate_descriptor, list) or len(candidate_descriptor) != 128:
                return JsonResponse({
                    "status": "error",
                    "code": "INVALID_DESCRIPTOR",
                    "message": "⚠️ Invalid biometric face vector. Please scan again."
                }, status=400)
        except Exception as e:
            return JsonResponse({
                "status": "error",
                "code": "INVALID_DESCRIPTOR_FORMAT",
                "message": f"Biometric decoding error: {e}"
            }, status=400)

        # Distance threshold (<= 0.55 genuine match)
        is_match, distance = face_profile.verify_descriptor(candidate_descriptor, threshold=0.55)
        if not is_match:
            FaceVerificationLog.objects.create(
                user=user,
                action=f"Punch ({action})",
                status="Mismatch",
                confidence_distance=round(distance, 4),
                ip_address=client_ip,
                user_agent=user_agent,
                message=f"Biometric mismatch: distance {distance:.3f} exceeded 0.55 threshold."
            )
            return JsonResponse({
                "status": "error",
                "code": "FACE_MISMATCH",
                "message": (
                    f"❌ Face Not Recognized! Biometric verification mismatch (Score: {distance:.2f}). "
                    "Only the registered employee can punch attendance."
                )
            }, status=400)

        # Log successful biometric match
        FaceVerificationLog.objects.create(
            user=user,
            action=f"Punch ({action})",
            status="Verified",
            confidence_distance=round(distance, 4),
            ip_address=client_ip,
            user_agent=user_agent,
            message=f"Genuine biometric match confirmed (Score: {distance:.3f})."
        )

    # 3. Concurrency-Safe Attendance Transaction
    with transaction.atomic():
        record = DailyAttendance.objects.select_for_update().filter(
            user=user,
            date=today
        ).first()

        if not action:
            if not record or not record.punch_in:
                action = "punch_in"
            elif not record.punch_out:
                action = "punch_out"
            else:
                return JsonResponse({
                    "status": "already_punched",
                    "message": "⚠️ You have already completed both Punch In and Punch Out for today."
                })

        # PUNCH IN
        if action == "punch_in":
            if record and record.punch_in:
                return JsonResponse({
                    "status": "error",
                    "code": "ALREADY_PUNCHED_IN",
                    "message": f"⚠️ You have already punched in today at {record.punch_in.strftime('%I:%M:%S %p')}."
                }, status=400)

            if not record:
                record = DailyAttendance(user=user, date=today)

            record.punch_in = current_time
            record.punch_in_latitude = latitude
            record.punch_in_longitude = longitude
            record.punch_in_location = location
            record.verification_method = verification_method
            record.face_verified_in = not is_exempt
            record.confidence_score = 1.0 if is_exempt else 0.95
            record.update_calculations()
            record.save()

            formatted_time = current_time.strftime("%I:%M:%S %p")
            status_desc = "Present / On Time" if not record.is_late else f"Late ({record.late_minutes} min delay)"

            return JsonResponse({
                "status": "punchin",
                "time": formatted_time,
                "date": today.strftime("%Y-%m-%d"),
                "status_desc": status_desc,
                "is_late": record.is_late,
                "late_minutes": record.late_minutes,
                "location": location,
                "message": f"✅ Punch In recorded at {formatted_time}! Status: {status_desc}."
            })

        # PUNCH OUT
        elif action == "punch_out":
            if not record or not record.punch_in:
                return JsonResponse({
                    "status": "error",
                    "code": "NO_PUNCH_IN",
                    "message": "⚠️ Cannot Punch Out: No Punch In record exists for today."
                }, status=400)

            if record.punch_out:
                return JsonResponse({
                    "status": "error",
                    "code": "ALREADY_PUNCHED_OUT",
                    "message": f"⚠️ You have already punched out today at {record.punch_out.strftime('%I:%M:%S %p')}."
                }, status=400)

            # Warning if punching out before 6:30 PM
            if current_time < OFFICE_CLOSING_TIME and not confirm_early:
                in_sec = record.punch_in.hour * 3600 + record.punch_in.minute * 60 + record.punch_in.second
                cur_sec = current_time.hour * 3600 + current_time.minute * 60 + current_time.second
                diff_sec = max(0, cur_sec - in_sec)
                hours_worked = diff_sec // 3600
                mins_worked = (diff_sec % 3600) // 60
                return JsonResponse({
                    "status": "warning_early_punchout",
                    "closing_time": OFFICE_CLOSING_TIME.strftime("%I:%M %p"),
                    "working_duration": f"{hours_worked} hrs {mins_worked} mins",
                    "message": (
                        f"⚠️ Early Punch Out Notice: Office shift ends at 6:30 PM. "
                        f"You have worked {hours_worked} hrs {mins_worked} mins so far. "
                        "Are you sure you want to punch out now?"
                    )
                })

            record.punch_out = current_time
            record.punch_out_latitude = latitude
            record.punch_out_longitude = longitude
            record.punch_out_location = location
            record.face_verified_out = not is_exempt
            record.update_calculations()
            record.save()

            formatted_time = current_time.strftime("%I:%M:%S %p")
            working_str = record.formatted_working_hours

            return JsonResponse({
                "status": "punchout",
                "time": formatted_time,
                "date": today.strftime("%Y-%m-%d"),
                "working_hours": working_str,
                "location": location,
                "message": f"👋 Punch Out recorded at {formatted_time}! Total Working Hours: {working_str}."
            })

        return JsonResponse({"status": "error", "message": f"Invalid action: {action}"}, status=400)


@login_required
@require_http_methods(["POST"])
def api_enroll_face(request):
    """
    Saves biometric face samples and computes a robust centroid descriptor vector.
    Can accept a single 128-d vector or a list of multiple sample vectors (e.g. 3-5).
    """
    target_user = request.user
    target_user_id = request.POST.get("user_id")

    if target_user_id:
        if not (request.user.is_staff or request.user.is_superuser):
            return JsonResponse({"status": "error", "message": "Unauthorized to enroll other employees."}, status=403)
        target_user = get_object_or_404(User, id=target_user_id)

    raw_data = request.POST.get("face_descriptor")
    liveness_score = float(request.POST.get("liveness_score", "1.0") or 1.0)

    if not raw_data:
        return JsonResponse({"status": "error", "message": "No descriptor data provided."}, status=400)

    try:
        parsed = json.loads(raw_data)
        samples = []

        if isinstance(parsed, list):
            if len(parsed) > 0 and isinstance(parsed[0], list):
                # Multi-sample list
                samples = parsed
            elif len(parsed) == 128:
                # Single 128-d sample
                samples = [parsed]
            else:
                return JsonResponse({"status": "error", "message": "Invalid vector length."}, status=400)
        else:
            return JsonResponse({"status": "error", "message": "Invalid descriptor payload."}, status=400)

        centroid = compute_centroid_descriptor(samples)
        if not centroid or len(centroid) != 128:
            return JsonResponse({"status": "error", "message": "Failed to compute valid face centroid."}, status=400)

        face_profile, _ = EmployeeFaceProfile.objects.get_or_create(user=target_user)
        face_profile.face_descriptor = centroid
        face_profile.enrollment_status = 'REGISTERED'
        face_profile.is_enrolled = True
        face_profile.sample_count = len(samples)
        face_profile.enrolled_at = timezone.now()
        face_profile.enrolled_by = request.user
        face_profile.anti_spoofing_passed = True
        face_profile.liveness_score = liveness_score
        face_profile.save()

        FaceVerificationLog.objects.create(
            user=target_user,
            action="Enrollment",
            status="Registered",
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:250],
            message=f"Biometrics registered with {len(samples)} captured sample(s) by {request.user.username}."
        )

        return JsonResponse({
            "status": "success",
            "message": f"✅ Biometric face profile registered successfully for {target_user.username} ({len(samples)} samples processed)!"
        })
    except Exception as e:
        logger.error(f"Face enrollment error: {e}")
        return JsonResponse({"status": "error", "message": f"Enrollment error: {e}"}, status=400)


@admin_required
@require_http_methods(["POST"])
def api_reset_face(request):
    """Admin-only endpoint to clear and reset an employee's face biometric data."""
    target_user_id = request.POST.get("user_id")
    target_user = get_object_or_404(User, id=target_user_id)

    face_profile, _ = EmployeeFaceProfile.objects.get_or_create(user=target_user)
    face_profile.face_descriptor = None
    face_profile.enrollment_status = 'RE_ENROLLMENT_REQUIRED'
    face_profile.is_enrolled = False
    face_profile.enrolled_at = None
    face_profile.sample_count = 0
    face_profile.anti_spoofing_passed = False
    face_profile.save()

    FaceVerificationLog.objects.create(
        user=target_user,
        action="Reset Biometrics",
        status="Reset Required",
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:250],
        message=f"Biometric profile reset by admin {request.user.username}. Re-enrollment required."
    )

    messages.success(request, f"Biometric template for {target_user.username} has been reset. Status: Re-enrollment Required.")
    return redirect("admin_face_management")


# =========================================================
# ADMINISTRATOR DASHBOARD VIEWS
# =========================================================

@admin_required
def admin_dashboard(request):
    """Main Administrator Dashboard."""
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    kpis = get_admin_dashboard_kpis()
    records_qs = DailyAttendance.objects.filter(date=today).select_related('user', 'user__profile', 'user__profile__department')

    status_filter = request.GET.get("status")
    search_q = request.GET.get("q", "").strip()

    evaluated_records = []
    for r in records_qs:
        eval_row = evaluate_attendance_row(user=r.user, date_obj=today, record=r)
        eval_row["employee"] = r.user
        eval_row["department"] = getattr(r.user.profile, 'department', None) if hasattr(r.user, 'profile') else None

        if search_q:
            q_lower = search_q.lower()
            u_name = f"{r.user.first_name} {r.user.last_name}".lower()
            if q_lower not in r.user.username.lower() and q_lower not in u_name and q_lower not in r.user.email.lower():
                continue

        if status_filter and status_filter.lower() not in eval_row["status"].lower():
            continue

        evaluated_records.append(eval_row)

    pending_leaves_count = LeaveRequest.objects.filter(status='Pending').count()
    pending_corrections_count = AttendanceCorrectionRequest.objects.filter(status='Pending').count()

    context = {
        "kpis": kpis,
        "evaluated_records": evaluated_records,
        "today_date": today,
        "search_q": search_q,
        "status_filter": status_filter,
        "pending_leaves_count": pending_leaves_count,
        "pending_corrections_count": pending_corrections_count,
        "office_start_time": OFFICE_START_TIME.strftime("%I:%M %p"),
        "on_time_end_time": ON_TIME_END_TIME.strftime("%I:%M %p"),
        "office_closing_time": OFFICE_CLOSING_TIME.strftime("%I:%M %p"),
    }

    return render(request, "admin_dashboard.html", context)


@admin_required
def admin_attendance(request):
    """All attendance records management view."""
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    emp_id = request.GET.get("employee")
    date_val = request.GET.get("date")
    month_val = request.GET.get("month")
    year_val = request.GET.get("year", str(today.year))
    status_filter = request.GET.get("status")
    search_q = request.GET.get("q", "").strip()

    qs = DailyAttendance.objects.select_related('user', 'user__profile').order_by('-date', '-punch_in')

    if emp_id:
        qs = qs.filter(user_id=emp_id)

    if date_val:
        try:
            d = datetime.strptime(date_val, "%Y-%m-%d").date()
            qs = qs.filter(date=d)
        except ValueError:
            pass
    elif month_val:
        try:
            qs = qs.filter(date__year=int(year_val), date__month=int(month_val))
        except ValueError:
            pass

    if search_q:
        qs = qs.filter(
            Q(user__username__icontains=search_q) |
            Q(user__first_name__icontains=search_q) |
            Q(user__last_name__icontains=search_q) |
            Q(user__email__icontains=search_q)
        )

    evaluated_records = []
    for r in qs:
        eval_row = evaluate_attendance_row(user=r.user, date_obj=r.date, record=r)
        eval_row["employee"] = r.user
        eval_row["department"] = getattr(r.user.profile, 'department', None) if hasattr(r.user, 'profile') else None

        if status_filter and status_filter.lower() not in eval_row["status"].lower():
            continue

        evaluated_records.append(eval_row)

    paginator = Paginator(evaluated_records, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    all_employees = User.objects.filter(is_active=True).order_by('username')
    all_departments = Department.objects.all()

    context = {
        "page_obj": page_obj,
        "all_employees": all_employees,
        "all_departments": all_departments,
        "emp_id": emp_id,
        "date_val": date_val,
        "month_val": month_val,
        "year_val": year_val,
        "status_filter": status_filter,
        "search_q": search_q,
    }

    return render(request, "admin_attendance.html", context)


@admin_required
@require_http_methods(["POST"])
def admin_attendance_correct(request, record_id):
    """Admin manually corrects attendance record with mandatory reason and audit trail."""
    record = get_object_or_404(DailyAttendance, id=record_id)
    reason = request.POST.get("reason", "").strip()
    punch_in_str = request.POST.get("punch_in", "").strip()
    punch_out_str = request.POST.get("punch_out", "").strip()

    if not reason:
        messages.error(request, "⚠️ A mandatory reason is required to correct attendance records.")
        return redirect(request.META.get("HTTP_REFERER", "admin_attendance"))

    old_p_in = record.punch_in
    old_p_out = record.punch_out

    try:
        new_p_in = datetime.strptime(punch_in_str, "%H:%M:%S").time() if len(punch_in_str.split(':')) == 3 else datetime.strptime(punch_in_str, "%H:%M").time() if punch_in_str else None
    except ValueError:
        new_p_in = old_p_in

    try:
        new_p_out = datetime.strptime(punch_out_str, "%H:%M:%S").time() if len(punch_out_str.split(':')) == 3 else datetime.strptime(punch_out_str, "%H:%M").time() if punch_out_str else None
    except ValueError:
        new_p_out = old_p_out

    record.punch_in = new_p_in
    record.punch_out = new_p_out
    record.verification_method = "manual_admin"
    record.notes = f"Admin corrected by {request.user.username}: {reason}"
    record.update_calculations()
    record.save()

    AttendanceAuditLog.objects.create(
        attendance=record,
        modified_by=request.user,
        old_punch_in=old_p_in,
        old_punch_out=old_p_out,
        new_punch_in=new_p_in,
        new_punch_out=new_p_out,
        reason=reason
    )

    messages.success(request, f"✅ Attendance record for {record.user.username} on {record.date} updated successfully.")
    return redirect(request.META.get("HTTP_REFERER", "admin_attendance"))


@admin_required
def admin_employees(request):
    """Employee directory management."""
    search_q = request.GET.get("q", "").strip()
    dept_id = request.GET.get("department")

    qs = User.objects.select_related('profile', 'profile__department', 'face_profile').order_by('-date_joined')

    if search_q:
        qs = qs.filter(
            Q(username__icontains=search_q) |
            Q(first_name__icontains=search_q) |
            Q(last_name__icontains=search_q) |
            Q(email__icontains=search_q) |
            Q(profile__employee_id__icontains=search_q)
        )

    if dept_id:
        qs = qs.filter(profile__department_id=dept_id)

    departments = Department.objects.all()

    context = {
        "employees": qs,
        "departments": departments,
        "search_q": search_q,
        "dept_id": dept_id,
    }

    return render(request, "admin_employees.html", context)


@admin_required
@require_http_methods(["POST"])
def admin_employee_add(request):
    """Add a new employee from the admin panel."""
    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    dept_id = request.POST.get("department")
    designation = request.POST.get("designation", "").strip()
    phone = request.POST.get("phone", "").strip()
    face_exempt = request.POST.get("face_auth_exempt") == "on"
    exemption_reason = request.POST.get("exemption_reason", "").strip()

    if not username or not password:
        messages.error(request, "⚠️ Username and password are required.")
        return redirect("admin_employees")

    if User.objects.filter(username=username).exists():
        messages.error(request, f"⚠️ Username '{username}' already exists.")
        return redirect("admin_employees")

    try:
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name
        )

        dept = Department.objects.filter(id=dept_id).first() if dept_id else None
        emp_id = f"EMP{user.id:04d}"

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.employee_id = emp_id
        profile.department = dept
        profile.designation = designation or "Employee"
        profile.phone = phone
        profile.face_auth_exempt = face_exempt
        profile.exemption_reason = exemption_reason
        profile.save()

        EmployeeFaceProfile.objects.get_or_create(user=user)

        messages.success(request, f"✅ Employee {user.username} created successfully (ID: {emp_id}).")
    except Exception as e:
        logger.error(f"Error adding employee: {e}")
        messages.error(request, f"Error creating employee: {e}")

    return redirect("admin_employees")


@admin_required
@require_http_methods(["POST"])
def admin_employee_edit(request, user_id):
    """Edit an existing employee details."""
    user = get_object_or_404(User, id=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=user)

    user.first_name = request.POST.get("first_name", "").strip()
    user.last_name = request.POST.get("last_name", "").strip()
    user.email = request.POST.get("email", "").strip()
    user.save()

    dept_id = request.POST.get("department")
    profile.department = Department.objects.filter(id=dept_id).first() if dept_id else None
    profile.designation = request.POST.get("designation", "").strip()
    profile.phone = request.POST.get("phone", "").strip()
    profile.face_auth_exempt = request.POST.get("face_auth_exempt") == "on"
    profile.exemption_reason = request.POST.get("exemption_reason", "").strip()
    profile.save()

    messages.success(request, f"✅ Employee {user.username} updated successfully.")
    return redirect("admin_employees")


@admin_required
@require_http_methods(["POST"])
def admin_employee_toggle_status(request, user_id):
    """Activate or deactivate employee account."""
    user = get_object_or_404(User, id=user_id)
    if user == request.user:
        messages.error(request, "⚠️ You cannot deactivate your own account.")
        return redirect("admin_employees")

    user.is_active = not user.is_active
    user.save()

    status_str = "activated" if user.is_active else "deactivated"
    messages.success(request, f"Employee {user.username} has been {status_str}.")
    return redirect("admin_employees")


@admin_required
@require_http_methods(["POST"])
def admin_employee_reset_password(request, user_id):
    """Admin resets employee password."""
    user = get_object_or_404(User, id=user_id)
    new_password = request.POST.get("new_password", "").strip()

    if not new_password or len(new_password) < 6:
        messages.error(request, "⚠️ New password must be at least 6 characters long.")
        return redirect("admin_employees")

    user.set_password(new_password)
    user.save()

    messages.success(request, f"✅ Password for {user.username} has been reset successfully.")
    return redirect("admin_employees")


@admin_required
def admin_face_management(request):
    """
    Dedicated Face Scanner & Verification Management View:
    - Lists all employees with enrollment status: Not Registered, Registration Pending, Registered, Re-enrollment Required
    - Audit log of recent face verification attempts
    - Actions to authorize enrollment, reset biometric templates
    """
    employees = User.objects.select_related('profile', 'profile__department', 'face_profile').order_by('username')
    recent_logs = FaceVerificationLog.objects.select_related('user').order_by('-timestamp')[:50]

    status_counts = {
        "registered": EmployeeFaceProfile.objects.filter(enrollment_status='REGISTERED').count(),
        "pending": EmployeeFaceProfile.objects.filter(enrollment_status='PENDING').count(),
        "re_enrollment": EmployeeFaceProfile.objects.filter(enrollment_status='RE_ENROLLMENT_REQUIRED').count(),
        "not_registered": EmployeeFaceProfile.objects.filter(enrollment_status='NOT_REGISTERED').count(),
    }

    return render(request, "admin_face_management.html", {
        "employees": employees,
        "recent_logs": recent_logs,
        "status_counts": status_counts,
    })


@admin_required
def admin_leaves(request):
    """Manage and approve/reject leave requests."""
    status_filter = request.GET.get("status", "")
    qs = LeaveRequest.objects.select_related('user', 'reviewed_by').order_by('-created_at')

    if status_filter:
        qs = qs.filter(status=status_filter)

    return render(request, "admin_leaves.html", {
        "leaves": qs,
        "status_filter": status_filter,
    })


@admin_required
@require_http_methods(["POST"])
def admin_leave_action(request, leave_id):
    """Approve or reject leave request with admin remark."""
    leave = get_object_or_404(LeaveRequest, id=leave_id)
    action = request.POST.get("action", "")
    remark = request.POST.get("admin_remark", "").strip()

    if action in ["Approved", "Rejected"]:
        leave.status = action
        leave.reviewed_by = request.user
        leave.reviewed_at = timezone.now()
        leave.admin_remark = remark
        leave.save()

        messages.success(request, f"Leave request for {leave.user.username} has been {action}.")
    else:
        messages.error(request, "Invalid leave action.")

    return redirect("admin_leaves")


@admin_required
def admin_holidays(request):
    """List and manage company holidays."""
    holidays = Holiday.objects.all().order_by('date')
    return render(request, "admin_holidays.html", {"holidays": holidays})


@admin_required
@require_http_methods(["POST"])
def admin_holiday_add(request):
    """Add a new company holiday."""
    name = request.POST.get("name", "").strip()
    date_str = request.POST.get("date", "").strip()
    description = request.POST.get("description", "").strip()

    if not name or not date_str:
        messages.error(request, "⚠️ Holiday name and date are required.")
        return redirect("admin_holidays")

    try:
        h_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        Holiday.objects.create(name=name, date=h_date, description=description)
        messages.success(request, f"✅ Holiday '{name}' added successfully.")
    except Exception as e:
        messages.error(request, f"Error adding holiday: {e}")

    return redirect("admin_holidays")


@admin_required
@require_http_methods(["POST"])
def admin_holiday_delete(request, holiday_id):
    """Delete a company holiday."""
    holiday = get_object_or_404(Holiday, id=holiday_id)
    name = holiday.name
    holiday.delete()
    messages.success(request, f"Holiday '{name}' deleted.")
    return redirect("admin_holidays")


@admin_required
def admin_reports(request):
    """Comprehensive attendance reports for all employees."""
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    try:
        month = int(request.GET.get("month", today.month))
        year = int(request.GET.get("year", today.year))
    except (ValueError, TypeError):
        month, year = today.month, today.year

    dept_id = request.GET.get("department")
    emp_id = request.GET.get("employee")

    employees_qs = User.objects.filter(is_active=True).select_related('profile', 'profile__department')
    if dept_id:
        employees_qs = employees_qs.filter(profile__department_id=dept_id)
    if emp_id:
        employees_qs = employees_qs.filter(id=emp_id)

    reports_data = []
    for emp in employees_qs:
        m_data = get_monthly_attendance_data(emp, year, month)
        reports_data.append({
            "employee": emp,
            "profile": getattr(emp, 'profile', None),
            "summary": m_data["summary"],
            "days": m_data["days"],
        })

    month_names = [(i, datetime(2000, i, 1).strftime("%B")) for i in range(1, 13)]
    years_list = range(today.year - 2, today.year + 2)

    context = {
        "reports_data": reports_data,
        "year": year,
        "month": month,
        "month_name": datetime(2000, month, 1).strftime("%B"),
        "month_names": month_names,
        "years_list": years_list,
        "departments": Department.objects.all(),
        "employees": User.objects.filter(is_active=True),
        "selected_dept": dept_id,
        "selected_emp": emp_id,
    }

    return render(request, "admin_reports.html", context)


@admin_required
def export_attendance_csv(request):
    """Export filtered attendance records to CSV."""
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    month = int(request.GET.get("month", today.month))
    year = int(request.GET.get("year", today.year))
    emp_id = request.GET.get("employee")

    employees = User.objects.filter(is_active=True)
    if emp_id:
        employees = employees.filter(id=emp_id)

    flattened_rows = []
    for emp in employees:
        m_data = get_monthly_attendance_data(emp, year, month)
        for day_row in m_data["days"]:
            flattened_rows.append({
                "employee_name": f"{emp.first_name} {emp.last_name}".strip() or emp.username,
                "username": emp.username,
                "date": day_row["date"],
                "day": day_row["day"],
                "punch_in": day_row["punch_in_formatted"],
                "punch_out": day_row["punch_out_formatted"],
                "working_hours": day_row["working_hours"],
                "status": day_row["status"],
                "delay": day_row["delay"],
                "location": day_row["location"],
                "verification_method": day_row["verification_method"],
            })

    csv_data = generate_attendance_csv(flattened_rows)
    response = HttpResponse(csv_data, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="attendance_{year}_{month:02d}.csv"'
    return response


@admin_required
def export_attendance_excel(request):
    """Export filtered attendance records to styled Excel (.xlsx)."""
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    month = int(request.GET.get("month", today.month))
    year = int(request.GET.get("year", today.year))
    emp_id = request.GET.get("employee")

    employees = User.objects.filter(is_active=True)
    if emp_id:
        employees = employees.filter(id=emp_id)

    flattened_rows = []
    for emp in employees:
        m_data = get_monthly_attendance_data(emp, year, month)
        for day_row in m_data["days"]:
            flattened_rows.append({
                "employee_name": f"{emp.first_name} {emp.last_name}".strip() or emp.username,
                "username": emp.username,
                "date": day_row["date"],
                "day": day_row["day"],
                "punch_in": day_row["punch_in_formatted"],
                "punch_out": day_row["punch_out_formatted"],
                "working_hours": day_row["working_hours"],
                "status": day_row["status"],
                "delay": day_row["delay"],
                "location": day_row["location"],
                "verification_method": day_row["verification_method"],
            })

    month_str = datetime(2000, month, 1).strftime("%B")
    excel_data = generate_attendance_excel(flattened_rows, title=f"Report {month_str} {year}")
    response = HttpResponse(
        excel_data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="attendance_{year}_{month:02d}.xlsx"'
    return response


@admin_required
def admin_audit_logs(request):
    """Display audit logs of attendance corrections."""
    logs = AttendanceAuditLog.objects.select_related('attendance', 'attendance__user', 'modified_by').order_by('-created_at')
    return render(request, "admin_audit_logs.html", {"logs": logs})


# Backward compatibility aliases
index = employee_dashboard
attendance_report = employee_attendance
apply_leave = employee_apply_leave
request_correction = employee_profile
change_password = employee_profile