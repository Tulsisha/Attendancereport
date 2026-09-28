from django.contrib import admin
from .models import (
    DailyAttendance,
    UserProfile,
    EmployeeFaceProfile,
    Department,
    Holiday,
    LeaveRequest,
    AttendanceCorrectionRequest,
    AttendanceAuditLog
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'employee_id', 'department', 'designation', 'phone', 'face_auth_exempt')
    list_filter = ('department', 'face_auth_exempt')
    search_fields = ('user__username', 'user__email', 'employee_id')


@admin.register(EmployeeFaceProfile)
class EmployeeFaceProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_enrolled', 'enrolled_at', 'enrolled_by', 'anti_spoofing_passed')
    list_filter = ('is_enrolled', 'anti_spoofing_passed')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('enrolled_at', 'updated_at')


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'description')
    list_filter = ('date',)
    search_fields = ('name',)


@admin.register(DailyAttendance)
class DailyAttendanceAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'date',
        'punch_in',
        'punch_out',
        'is_late',
        'late_minutes',
        'working_hours',
        'verification_method',
        'face_verified_in',
        'face_verified_out'
    )
    list_filter = ('date', 'is_late', 'verification_method', 'user')
    search_fields = ('user__username', 'user__email', 'punch_in_location')
    readonly_fields = ('working_hours', 'late_minutes', 'is_late')


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'leave_type', 'from_date', 'to_date', 'total_days', 'status', 'created_at')
    list_filter = ('status', 'leave_type', 'from_date')
    search_fields = ('user__username', 'reason')


@admin.register(AttendanceCorrectionRequest)
class AttendanceCorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'requested_punch_in', 'requested_punch_out', 'status', 'created_at')
    list_filter = ('status', 'date')
    search_fields = ('user__username', 'reason')


@admin.register(AttendanceAuditLog)
class AttendanceAuditLogAdmin(admin.ModelAdmin):
    list_display = ('attendance', 'modified_by', 'old_punch_in', 'new_punch_in', 'old_punch_out', 'new_punch_out', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('attendance__user__username', 'modified_by__username', 'reason')
    readonly_fields = ('created_at',)
