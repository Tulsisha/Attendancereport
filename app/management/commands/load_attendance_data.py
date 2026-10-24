from pathlib import Path
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connection
from django.apps import apps
from django.contrib.auth.models import User
from app.models import (
    Department,
    DailyAttendance,
    Holiday,
    UserProfile,
    EmployeeFaceProfile,
    LeaveRequest,
)


class Command(BaseCommand):
    help = "Safely loads initial attendance data fixture into PostgreSQL/database without creating duplicates."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force load fixture even if data already exists in database',
        )

    def handle(self, *args, **options):
        force = options.get('force', False)
        existing_users = User.objects.count()
        existing_attendance = DailyAttendance.objects.count()

        if existing_users > 0 and not force:
            self.stdout.write(self.style.WARNING(
                f"[SKIP] Database already contains {existing_users} user(s) and "
                f"{existing_attendance} attendance record(s). "
                "Skipping fixture loading to prevent duplicates. "
                "Use --force to reload data anyway."
            ))
            return

        fixture_path = Path(__file__).resolve().parent.parent.parent.parent / 'fixtures' / 'initial_attendance_data.json'
        if not fixture_path.exists():
            self.stderr.write(self.style.ERROR(f"Fixture file not found at: {fixture_path}"))
            return

        self.stdout.write(self.style.NOTICE(f"Loading initial attendance data from {fixture_path.name}..."))
        call_command('loaddata', str(fixture_path), verbosity=1)

        # Reset Postgres sequences to prevent primary key collision on future inserts
        if connection.vendor == 'postgresql':
            self.stdout.write(self.style.NOTICE("Resetting PostgreSQL sequences..."))
            app_models = apps.get_models()
            sequence_sql = connection.ops.sequence_reset_sql(no_style(), app_models)
            with connection.cursor() as cursor:
                for sql in sequence_sql:
                    cursor.execute(sql)
            self.stdout.write(self.style.SUCCESS("PostgreSQL sequences reset successfully."))

        self.stdout.write(self.style.SUCCESS(
            f"Successfully populated database:\n"
            f" - Users: {User.objects.count()}\n"
            f" - User Profiles: {UserProfile.objects.count()}\n"
            f" - Face Profiles: {EmployeeFaceProfile.objects.count()}\n"
            f" - Departments: {Department.objects.count()}\n"
            f" - Holidays: {Holiday.objects.count()}\n"
            f" - Daily Attendance Records: {DailyAttendance.objects.count()}\n"
            f" - Leave Requests: {LeaveRequest.objects.count()}"
        ))
