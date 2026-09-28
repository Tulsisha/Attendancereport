from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date
from app.models import Department, UserProfile, EmployeeFaceProfile, Holiday, DailyAttendance


class Command(BaseCommand):
    help = "Initializes default departments, holidays, ensures user profiles exist, and creates default admin if needed."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Initializing Attendance Management System..."))

        # 1. Default Departments
        departments = ["Engineering", "Human Resources", "Operations", "Sales & Marketing", "Finance"]
        for dept_name in departments:
            d, created = Department.objects.get_or_create(name=dept_name)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created department: {dept_name}"))

        default_dept = Department.objects.first()

        # 2. Sync User Profiles & Face Profiles
        for u in User.objects.all():
            p, _ = UserProfile.objects.get_or_create(user=u)
            if not p.employee_id:
                p.employee_id = f"EMP{u.id:04d}"
            if not p.department:
                p.department = default_dept
            p.save()
            EmployeeFaceProfile.objects.get_or_create(user=u)

        self.stdout.write(self.style.SUCCESS(f"Synced profiles for {User.objects.count()} user(s)."))

        # 3. Default Admin Account check
        if not User.objects.filter(is_superuser=True).exists():
            admin_user = User.objects.create_superuser(
                username="admin",
                email="admin@example.com",
                password="adminpassword123",
                first_name="System",
                last_name="Administrator"
            )
            admin_prof, _ = UserProfile.objects.get_or_create(user=admin_user)
            admin_prof.employee_id = "ADM0001"
            admin_prof.department = default_dept
            admin_prof.designation = "Super Administrator"
            admin_prof.save()
            self.stdout.write(self.style.SUCCESS("Created default superuser: admin / adminpassword123"))
        else:
            self.stdout.write(self.style.SUCCESS("Existing superuser found."))

        # 4. Default Annual Holidays
        year = timezone.localtime(timezone.now()).year
        holidays_data = [
            ("New Year's Day", date(year, 1, 1), "Global New Year celebration"),
            ("Republic Day", date(year, 1, 26), "National Republic Day"),
            ("Independence Day", date(year, 8, 15), "National Independence Day"),
            ("Gandhi Jayanti", date(year, 10, 2), "Mahatma Gandhi Birthday"),
            ("Christmas Day", date(year, 12, 25), "Christmas holiday"),
        ]
        for name, h_date, desc in holidays_data:
            Holiday.objects.get_or_create(
                date=h_date,
                defaults={"name": name, "description": desc}
            )

        self.stdout.write(self.style.SUCCESS(f"Configured official holidays for {year}."))

        # 5. Recalculate working hours on existing attendance records
        for att in DailyAttendance.objects.all():
            att.update_calculations()
            att.save()

        self.stdout.write(self.style.SUCCESS(f"Recalculated {DailyAttendance.objects.count()} attendance record(s)."))
        self.stdout.write(self.style.SUCCESS("Setup completed successfully! System is ready to run."))
