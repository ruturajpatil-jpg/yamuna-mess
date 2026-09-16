# Yamuna Mess – V7

## Changes in V7
- First open screen is a single Login page with Student/Admin selection.
- Student registration now supports Username + Password.
- Admin can edit student name, roll, username, password, phone, course and photo.
- Admin can permanently remove a student.
- Admin can record Breakfast, Lunch, Evening Snacks and Dinner attendance.
- Student dashboard shows all four meal types.
- Attendance notification hook is prepared for WhatsApp Business/Cloud API.

## WhatsApp automatic notification
For true automatic WhatsApp sending, the Railway service must be connected to an approved WhatsApp Business/Cloud API setup. Configure these Railway variables:
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_ATTENDANCE_TEMPLATE` (approved template name)
- `WHATSAPP_TEMPLATE_LANGUAGE` (default `mr`)
- `WHATSAPP_API_VERSION` (default `v23.0`)

The app will send the configured template after an attendance record is saved. If these variables are not configured, attendance still saves normally and the app reports that WhatsApp setup is pending.

## Existing students
During migration, existing students receive a transitional login: Username = their existing Roll No.; Password = their existing Mobile No. Admin can edit these credentials afterward.
