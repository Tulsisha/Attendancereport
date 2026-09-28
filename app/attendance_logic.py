"""
Attendance business logic and helper utilities for the Attendance Management System.
Configured timezone: Asia/Kolkata.
Shift Timings:
- Office Start Time: 9:30 AM
- On-Time Punch In: 9:30 AM - 9:35 AM (inclusive)
- Late Punch In: After 9:35 AM
- Office Closing Time: 6:30 PM (9 Hours Shift)
"""

from datetime import datetime, date, time, timedelta
import calendar
import math
import io
import csv
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth.models import User
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from .models import (
    DailyAttendance,
    Holiday,
    LeaveRequest,
    EmployeeFaceProfile,
    UserProfile
)

# Shift Timing Constants
OFFICE_START_TIME = time(9, 30, 0)
ON_TIME_END_TIME = time(9, 35, 0)
OFFICE_CLOSING_TIME = time(18, 30, 0)
SHIFT_DURATION_HOURS = 9.0


def get_current_kolkata_datetime():
    """Returns the current timezone-aware datetime in Asia/Kolkata."""
    return timezone.localtime(timezone.now())


def is_sunday(date_obj):
    """Returns True if the given date is a Sunday."""
    return date_obj.weekday() == 6


def get_holiday_for_date(date_obj):
    """Returns Holiday object or None."""
    return Holiday.objects.filter(date=date_obj).first()


def calculate_late_minutes(punch_in_time):
    """
    Computes late minutes if punched in after 9:35 AM.
    Calculated relative to 9:30 AM office start.
    """
    if not punch_in_time:
        return 0
    if punch_in_time <= ON_TIME_END_TIME:
        return 0
    in_mins = punch_in_time.hour * 60 + punch_in_time.minute + (punch_in_time.second / 60.0)
    start_mins = OFFICE_START_TIME.hour * 60 + OFFICE_START_TIME.minute
    return max(0, int(round(in_mins - start_mins)))


def calculate_working_duration(punch_in_time, punch_out_time):
    """
    Computes total seconds, hours decimal, and formatted string.
    Returns (seconds, decimal_hours, formatted_string).
    """
    if not punch_in_time or not punch_out_time:
        return 0, 0.0, "-"
    in_sec = punch_in_time.hour * 3600 + punch_in_time.minute * 60 + punch_in_time.second
    out_sec = punch_out_time.hour * 3600 + punch_out_time.minute * 60 + punch_out_time.second
    diff_sec = max(0, out_sec - in_sec)
    hours = diff_sec // 3600
    mins = (diff_sec % 3600) // 60
    decimal_hrs = round(diff_sec / 3600.0, 2)
    return diff_sec, decimal_hrs, f"{hours} hrs {mins} mins"


def evaluate_attendance_row(user, date_obj, record=None, approved_leaves=None, holidays_map=None):
    """
    Evaluates status, colors, and metrics for a specific date and user.
    Handles synthetic absent / holiday / leave rows.
    """
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    # 1. Holiday (Sunday or configured company holiday)
    is_sun = date_obj.weekday() == 6
    holiday_item = holidays_map.get(date_obj) if holidays_map else get_holiday_for_date(date_obj)

    if is_sun or holiday_item:
        holiday_title = "Sunday" if is_sun else holiday_item.name
        return {
            "date": date_obj,
            "day": date_obj.strftime("%A"),
            "user": user,
            "punch_in": record.punch_in if record else None,
            "punch_out": record.punch_out if record else None,
            "punch_in_formatted": record.punch_in.strftime("%I:%M:%S %p") if (record and record.punch_in) else "-",
            "punch_out_formatted": record.punch_out.strftime("%I:%M:%S %p") if (record and record.punch_out) else "-",
            "working_hours": record.formatted_working_hours if record else "-",
            "status": "Holiday",
            "status_color": "blue",
            "row_class": "row-holiday",
            "badge_class": "badge-holiday",
            "delay": "-",
            "location": (record.punch_in_location if record and record.punch_in_location else "-"),
            "location_lat": record.punch_in_latitude if record else None,
            "location_lng": record.punch_in_longitude if record else None,
            "verification_method": record.verification_method if record else "-",
            "is_scheduled_day": False,
            "record_id": record.id if record else None,
            "notes": f"Holiday: {holiday_title}",
        }

    # 2. Approved Leave check
    is_on_leave = False
    leave_type = ""
    if approved_leaves:
        for lv in approved_leaves:
            if lv.from_date <= date_obj <= lv.to_date:
                is_on_leave = True
                leave_type = lv.leave_type
                break
    else:
        lv = LeaveRequest.objects.filter(
            user=user,
            status='Approved',
            from_date__lte=date_obj,
            to_date__gte=date_obj
        ).first()
        if lv:
            is_on_leave = True
            leave_type = lv.leave_type

    if is_on_leave and (not record or not record.punch_in):
        return {
            "date": date_obj,
            "day": date_obj.strftime("%A"),
            "user": user,
            "punch_in": None,
            "punch_out": None,
            "punch_in_formatted": "-",
            "punch_out_formatted": "-",
            "working_hours": "-",
            "status": f"Leave ({leave_type})",
            "status_color": "purple",
            "row_class": "row-leave",
            "badge_class": "badge-leave",
            "delay": "-",
            "location": "-",
            "location_lat": None,
            "location_lng": None,
            "verification_method": "-",
            "is_scheduled_day": False,
            "record_id": None,
            "notes": f"Approved {leave_type}",
        }

    # 3. If Record with Punch In exists
    if record and record.punch_in:
        punch_in_formatted = record.punch_in.strftime("%I:%M:%S %p")
        punch_out_formatted = record.punch_out.strftime("%I:%M:%S %p") if record.punch_out else "-"
        working_hours_str = record.formatted_working_hours

        # Case A: Punch in without punch out -> Punch Out Pending (Teal)
        if not record.punch_out:
            is_late_in = record.punch_in > ON_TIME_END_TIME
            delay_str = f"{record.late_minutes} mins late" if is_late_in else "On Time"
            return {
                "date": date_obj,
                "day": date_obj.strftime("%A"),
                "user": user,
                "punch_in": record.punch_in,
                "punch_out": None,
                "punch_in_formatted": punch_in_formatted,
                "punch_out_formatted": "-",
                "working_hours": "In Progress",
                "status": "Punch Out Pending",
                "status_color": "teal",
                "row_class": "row-pending",
                "badge_class": "badge-pending",
                "delay": delay_str,
                "location": record.punch_in_location or "Location unavailable",
                "location_lat": record.punch_in_latitude,
                "location_lng": record.punch_in_longitude,
                "verification_method": record.get_verification_method_display() if hasattr(record, 'get_verification_method_display') else record.verification_method,
                "is_scheduled_day": True,
                "record_id": record.id,
                "notes": f"Punch In recorded ({delay_str}). Awaiting Punch Out.",
            }

        # Case B: Punch In and Punch Out both exist
        if record.punch_in <= ON_TIME_END_TIME:
            # On-time -> Green
            return {
                "date": date_obj,
                "day": date_obj.strftime("%A"),
                "user": user,
                "punch_in": record.punch_in,
                "punch_out": record.punch_out,
                "punch_in_formatted": punch_in_formatted,
                "punch_out_formatted": punch_out_formatted,
                "working_hours": working_hours_str,
                "status": "Present / On Time",
                "status_color": "green",
                "row_class": "row-present",
                "badge_class": "badge-present",
                "delay": "On Time",
                "location": record.punch_in_location or "Location unavailable",
                "location_lat": record.punch_in_latitude,
                "location_lng": record.punch_in_longitude,
                "verification_method": record.get_verification_method_display() if hasattr(record, 'get_verification_method_display') else record.verification_method,
                "is_scheduled_day": True,
                "record_id": record.id,
                "notes": "Punched in within 9:30 AM - 9:35 AM.",
            }
        else:
            # Late -> Red
            late_mins = record.late_minutes if record.late_minutes > 0 else calculate_late_minutes(record.punch_in)
            return {
                "date": date_obj,
                "day": date_obj.strftime("%A"),
                "user": user,
                "punch_in": record.punch_in,
                "punch_out": record.punch_out,
                "punch_in_formatted": punch_in_formatted,
                "punch_out_formatted": punch_out_formatted,
                "working_hours": working_hours_str,
                "status": "Late",
                "status_color": "red",
                "row_class": "row-late",
                "badge_class": "badge-late",
                "delay": f"{late_mins} mins late",
                "location": record.punch_in_location or "Location unavailable",
                "location_lat": record.punch_in_latitude,
                "location_lng": record.punch_in_longitude,
                "verification_method": record.get_verification_method_display() if hasattr(record, 'get_verification_method_display') else record.verification_method,
                "is_scheduled_day": True,
                "record_id": record.id,
                "notes": f"Punched in after 9:35 AM (Delayed by {late_mins} mins).",
            }

    # 4. No Punch In
    if date_obj < today:
        # Completed historical working day without punch -> Absent (Yellow)
        return {
            "date": date_obj,
            "day": date_obj.strftime("%A"),
            "user": user,
            "punch_in": None,
            "punch_out": None,
            "punch_in_formatted": "-",
            "punch_out_formatted": "-",
            "working_hours": "-",
            "status": "Absent",
            "status_color": "yellow",
            "row_class": "row-absent",
            "badge_class": "badge-absent",
            "delay": "-",
            "location": "-",
            "location_lat": None,
            "location_lng": None,
            "verification_method": "-",
            "is_scheduled_day": True,
            "record_id": None,
            "notes": "No Punch In recorded for scheduled shift.",
        }
    elif date_obj == today:
        # Today: If after 6:30 PM, day has closed -> Absent (Yellow)
        if now_dt.time() >= OFFICE_CLOSING_TIME:
            return {
                "date": date_obj,
                "day": date_obj.strftime("%A"),
                "user": user,
                "punch_in": None,
                "punch_out": None,
                "punch_in_formatted": "-",
                "punch_out_formatted": "-",
                "working_hours": "-",
                "status": "Absent",
                "status_color": "yellow",
                "row_class": "row-absent",
                "badge_class": "badge-absent",
                "delay": "-",
                "location": "-",
                "location_lat": None,
                "location_lng": None,
                "verification_method": "-",
                "is_scheduled_day": True,
                "record_id": None,
                "notes": "Shift closed at 6:30 PM with no punch in.",
            }
        else:
            # During the day before 6:30 PM -> Not Punched In yet
            return {
                "date": date_obj,
                "day": date_obj.strftime("%A"),
                "user": user,
                "punch_in": None,
                "punch_out": None,
                "punch_in_formatted": "-",
                "punch_out_formatted": "-",
                "working_hours": "-",
                "status": "Not Marked",
                "status_color": "gray",
                "row_class": "row-not-marked",
                "badge_class": "badge-secondary",
                "delay": "-",
                "location": "-",
                "location_lat": None,
                "location_lng": None,
                "verification_method": "-",
                "is_scheduled_day": True,
                "record_id": None,
                "notes": "Shift in progress. Punch in pending.",
            }
    else:
        # Future date
        return {
            "date": date_obj,
            "day": date_obj.strftime("%A"),
            "user": user,
            "punch_in": None,
            "punch_out": None,
            "punch_in_formatted": "-",
            "punch_out_formatted": "-",
            "working_hours": "-",
            "status": "Scheduled",
            "status_color": "gray",
            "row_class": "",
            "badge_class": "badge-secondary",
            "delay": "-",
            "location": "-",
            "location_lat": None,
            "location_lng": None,
            "verification_method": "-",
            "is_scheduled_day": True,
            "record_id": None,
            "notes": "Upcoming scheduled shift.",
        }


def get_monthly_attendance_data(user, year, month):
    """
    Generates a complete day-by-day attendance report for the given user, month, and year.
    Returns:
    {
        "days": list of daily evaluated rows,
        "summary": {
            "total_days": int,
            "scheduled_working_days": int,
            "present_count": int,
            "late_count": int,
            "absent_count": int,
            "pending_count": int,
            "holiday_count": int,
            "leave_count": int,
            "attendance_percentage": float,
            "total_working_hours": float
        }
    }
    """
    total_days_in_month = calendar.monthrange(year, month)[1]

    # Pre-fetch records
    records = DailyAttendance.objects.filter(
        user=user,
        date__year=year,
        date__month=month
    )
    record_dict = {r.date: r for r in records}

    # Pre-fetch holidays
    holidays = Holiday.objects.filter(date__year=year, date__month=month)
    holidays_map = {h.date: h for h in holidays}

    # Pre-fetch approved leaves
    leaves = LeaveRequest.objects.filter(
        user=user,
        status='Approved',
        from_date__lte=date(year, month, total_days_in_month),
        to_date__gte=date(year, month, 1)
    )

    days_list = []
    present_count = 0
    late_count = 0
    absent_count = 0
    pending_count = 0
    holiday_count = 0
    leave_count = 0
    scheduled_working_days = 0
    total_work_seconds = 0

    for day in range(1, total_days_in_month + 1):
        curr_date = date(year, month, day)
        rec = record_dict.get(curr_date)
        eval_row = evaluate_attendance_row(
            user=user,
            date_obj=curr_date,
            record=rec,
            approved_leaves=leaves,
            holidays_map=holidays_map
        )
        days_list.append(eval_row)

        status = eval_row["status"]
        if eval_row["is_scheduled_day"]:
            scheduled_working_days += 1

        if status == "Present / On Time":
            present_count += 1
        elif status == "Late":
            late_count += 1
        elif status == "Punch Out Pending":
            pending_count += 1
        elif status == "Absent":
            absent_count += 1
        elif status == "Holiday":
            holiday_count += 1
        elif "Leave" in status:
            leave_count += 1

        if rec and rec.punch_in and rec.punch_out:
            sec, _, _ = calculate_working_duration(rec.punch_in, rec.punch_out)
            total_work_seconds += sec

    # Attendance percentage based on scheduled working days
    effective_present = present_count + late_count
    if scheduled_working_days > 0:
        att_percentage = round((effective_present / scheduled_working_days) * 100.0, 1)
    else:
        att_percentage = 100.0

    total_hours_decimal = round(total_work_seconds / 3600.0, 2)

    return {
        "days": days_list,
        "summary": {
            "total_days": total_days_in_month,
            "scheduled_working_days": scheduled_working_days,
            "present_count": present_count,
            "late_count": late_count,
            "absent_count": absent_count,
            "pending_count": pending_count,
            "holiday_count": holiday_count,
            "leave_count": leave_count,
            "attendance_percentage": att_percentage,
            "total_working_hours": total_hours_decimal,
        }
    }


def get_admin_dashboard_kpis():
    """
    Computes real-time admin KPIs for today.
    """
    now_dt = get_current_kolkata_datetime()
    today = now_dt.date()

    total_employees = User.objects.filter(is_active=True).count()

    today_records = DailyAttendance.objects.filter(date=today).select_related('user')

    present_today = 0
    late_today = 0
    pending_today = 0

    punched_user_ids = set()

    for r in today_records:
        punched_user_ids.add(r.user_id)
        if r.punch_in:
            if not r.punch_out:
                pending_today += 1
            elif r.punch_in <= ON_TIME_END_TIME:
                present_today += 1
            else:
                late_today += 1

    # Employees on leave today
    leaves_today = LeaveRequest.objects.filter(
        status='Approved',
        from_date__lte=today,
        to_date__gte=today
    ).values_list('user_id', flat=True)
    leave_user_ids = set(leaves_today)
    employees_on_leave = len(leave_user_ids)

    # Absent today
    is_today_holiday = is_sunday(today) or Holiday.objects.filter(date=today).exists()
    absent_today = 0
    if not is_today_holiday:
        if now_dt.time() >= OFFICE_CLOSING_TIME:
            # After 6:30 PM, anyone who didn't punch in and isn't on leave is absent
            absent_today = max(0, total_employees - len(punched_user_ids) - len(leave_user_ids))
        else:
            # Shift still ongoing: count users who haven't punched
            absent_today = 0

    # Total attendance this month
    total_this_month = DailyAttendance.objects.filter(
        date__year=today.year,
        date__month=today.month,
        punch_in__isnull=False
    ).count()

    return {
        "total_employees": total_employees,
        "present_today": present_today,
        "late_today": late_today,
        "pending_today": pending_today,
        "absent_today": absent_today,
        "employees_on_leave": employees_on_leave,
        "total_this_month": total_this_month,
        "today_date": today,
        "is_shift_closed": now_dt.time() >= OFFICE_CLOSING_TIME,
        "shift_hours": SHIFT_DURATION_HOURS,
    }


def generate_attendance_csv(records_data, filename_prefix="attendance_report"):
    """
    Generates a CSV response from records data.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Employee",
        "Username",
        "Date",
        "Day",
        "Punch In",
        "Punch Out",
        "Working Hours",
        "Status",
        "Delay / Note",
        "Location",
        "Verification"
    ])

    for row in records_data:
        writer.writerow([
            row.get("employee_name", ""),
            row.get("username", ""),
            row.get("date", ""),
            row.get("day", ""),
            row.get("punch_in", "-"),
            row.get("punch_out", "-"),
            row.get("working_hours", "-"),
            row.get("status", "-"),
            row.get("delay", "-"),
            row.get("location", "-"),
            row.get("verification_method", "-")
        ])

    return output.getvalue()


def generate_attendance_excel(records_data, title="Attendance Report"):
    """
    Generates a styled Excel workbook from records data using openpyxl.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance"

    # Header title
    ws.merge_cells("A1:K1")
    title_cell = ws["A1"]
    title_cell.value = f"Attendance Management System - {title}"
    title_cell.font = Font(name="Segoe UI", size=14, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color="072147", end_color="072147", fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 35

    headers = [
        "Employee", "Username", "Date", "Day", "Punch In",
        "Punch Out", "Working Hours", "Status", "Delay / Note",
        "Location", "Verification"
    ]
    ws.append(headers)
    ws.row_dimensions[2].height = 24

    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    thin_border = Border(
        left=Side(style='thin', color='E5E7EB'),
        right=Side(style='thin', color='E5E7EB'),
        top=Side(style='thin', color='E5E7EB'),
        bottom=Side(style='thin', color='E5E7EB')
    )

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    # Fills for status colors
    green_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    red_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    yellow_fill = PatternFill(start_color="FEF9C3", end_color="FEF9C3", fill_type="solid")
    teal_fill = PatternFill(start_color="CCFBF1", end_color="CCFBF1", fill_type="solid")
    blue_fill = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")

    row_idx = 3
    for row in records_data:
        data_row = [
            row.get("employee_name", ""),
            row.get("username", ""),
            str(row.get("date", "")),
            row.get("day", ""),
            row.get("punch_in", "-"),
            row.get("punch_out", "-"),
            row.get("working_hours", "-"),
            row.get("status", "-"),
            row.get("delay", "-"),
            row.get("location", "-"),
            row.get("verification_method", "-")
        ]
        ws.append(data_row)
        ws.row_dimensions[row_idx].height = 20

        status_text = row.get("status", "")
        row_fill = None
        if "Present" in status_text:
            row_fill = green_fill
        elif "Late" in status_text:
            row_fill = red_fill
        elif "Pending" in status_text:
            row_fill = teal_fill
        elif "Absent" in status_text:
            row_fill = yellow_fill
        elif "Holiday" in status_text:
            row_fill = blue_fill

        for col_idx in range(1, len(data_row) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = Font(name="Segoe UI", size=10)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center", horizontal="left" if col_idx in [1, 2, 10] else "center")
            if row_fill:
                cell.fill = row_fill

        row_idx += 1

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    excel_file = io.BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)
    return excel_file.getvalue()


def get_client_ip(request):
    """Extracts client IP address safely from request headers."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def compute_centroid_descriptor(samples):
    """
    Computes the centroid (mean) 128-d descriptor vector from multiple captured samples.
    Normalizes the vector to unit length for optimal Euclidean distance matching.
    """
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]

    dim = len(samples[0])
    centroid = [0.0] * dim
    valid_count = 0

    for s in samples:
        if isinstance(s, (list, tuple)) and len(s) == dim:
            for i in range(dim):
                centroid[i] += float(s[i])
            valid_count += 1

    if valid_count == 0:
        return None

    # Mean vector
    for i in range(dim):
        centroid[i] /= valid_count

    # Normalize to unit length
    magnitude = math.sqrt(sum(v ** 2 for v in centroid))
    if magnitude > 0:
        centroid = [round(v / magnitude, 6) for v in centroid]

    return centroid
