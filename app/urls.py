from django.urls import path
from . import views

urlpatterns = [
    # -----------------------------------------------------
    # Authentication & Root
    # -----------------------------------------------------
    path('', views.home, name='home'),
    path('loginn/', views.loginn, name='loginn'),
    path('loginform/', views.loginform, name='loginform'),
    path('logout/', views.user_logout, name='logout'),
    path('registr/', views.registr, name='registr'),
    path('registeruser/', views.registeruser, name='registeruser'),

    # -----------------------------------------------------
    # Dedicated Employee Portal Pages
    # -----------------------------------------------------
    path('employee/dashboard/', views.employee_dashboard, name='employee_dashboard'),
    path('employee/attendance/', views.employee_attendance, name='employee_attendance'),
    path('employee/scanner/', views.employee_scanner, name='employee_scanner'),
    path('employee/leave/apply/', views.employee_apply_leave, name='employee_apply_leave'),
    path('employee/leave/requests/', views.employee_leave_requests, name='employee_leave_requests'),
    path('employee/leave/cancel/<int:leave_id>/', views.employee_cancel_leave, name='employee_cancel_leave'),
    path('employee/profile/', views.employee_profile, name='employee_profile'),
    path('employee/attendance/export/csv/', views.employee_export_csv, name='employee_export_csv'),
    path('employee/attendance/export/excel/', views.employee_export_excel, name='employee_export_excel'),

    # -----------------------------------------------------
    # Employee Dashboard & Attendance Actions (Legacy Aliases)
    # -----------------------------------------------------
    path('index/', views.index, name='index'),
    path('punch/', views.punch, name='punch'),
    path('attendance-report/', views.attendance_report, name='attendance_report'),
    path('monthly-attendance/', views.attendance_report, name='monthly_attendance'),
    path('leave/apply/', views.apply_leave, name='apply_leave'),
    path('correction/request/', views.request_correction, name='request_correction'),
    path('change-password/', views.change_password, name='change_password'),

    # -----------------------------------------------------
    # Biometric Face Recognition APIs
    # -----------------------------------------------------
    path('api/face/challenge/', views.api_face_challenge, name='api_face_challenge'),
    path('api/face/status/', views.api_face_status, name='api_face_status'),
    path('api/face/enroll/', views.api_enroll_face, name='api_enroll_face'),
    path('api/face/reset/', views.api_reset_face, name='api_reset_face'),

    # -----------------------------------------------------
    # Administrator Dashboard & Management
    # -----------------------------------------------------
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-dashboard/attendance/', views.admin_attendance, name='admin_attendance'),
    path('admin-dashboard/attendance/correct/<int:record_id>/', views.admin_attendance_correct, name='admin_attendance_correct'),
    path('admin-dashboard/employees/', views.admin_employees, name='admin_employees'),
    path('admin-dashboard/employee/add/', views.admin_employee_add, name='admin_employee_add'),
    path('admin-dashboard/employee/edit/<int:user_id>/', views.admin_employee_edit, name='admin_employee_edit'),
    path('admin-dashboard/employee/toggle-status/<int:user_id>/', views.admin_employee_toggle_status, name='admin_employee_toggle_status'),
    path('admin-dashboard/employee/reset-password/<int:user_id>/', views.admin_employee_reset_password, name='admin_employee_reset_password'),
    path('admin-dashboard/leaves/', views.admin_leaves, name='admin_leaves'),
    path('admin-dashboard/leave/action/<int:leave_id>/', views.admin_leave_action, name='admin_leave_action'),
    path('admin-dashboard/holidays/', views.admin_holidays, name='admin_holidays'),
    path('admin-dashboard/holiday/add/', views.admin_holiday_add, name='admin_holiday_add'),
    path('admin-dashboard/holiday/delete/<int:holiday_id>/', views.admin_holiday_delete, name='admin_holiday_delete'),
    path('admin-dashboard/reports/', views.admin_reports, name='admin_reports'),
    path('admin-dashboard/reports/export/csv/', views.export_attendance_csv, name='export_attendance_csv'),
    path('admin-dashboard/reports/export/excel/', views.export_attendance_excel, name='export_attendance_excel'),
    path('admin-dashboard/face-management/', views.admin_face_management, name='admin_face_management'),
    path('admin-dashboard/audit-logs/', views.admin_audit_logs, name='admin_audit_logs'),
]