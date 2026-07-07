# Operational Guide

This guide provides instructions for common administrative tasks in ValuedCRM.

## 1. Setting Up ClickFunnels v2 Integration

### Generating an API Access Token
1. Log in to your ClickFunnels 2.0 account.
2. Navigate to **Team Settings** > **Developer Portal**.
3. Click **Add new platform application**.
4. Fill in the name (e.g., "ValuedCRM Integration") and click **Create platform application**.
5. Copy the **API Access Token** (Bearer Token) displayed.

### Configuring ValuedCRM
1. Go to the **ClickFunnels Configurations** section in the Django Admin.
2. Click "Add ClickFunnels Configuration".
3. Enter a name (e.g., "Main Team").
4. Paste the **API Access Token** into the field.
5. (Optional) Update the **API User Agent** (Default: `ValuedCRM/1.0`).
6. Check **Is Active**.
7. Click **Save**.

### Verifying Connection
1. In the list view, select the configuration.
2. Choose the action **Verify ClickFunnels Credentials** and click **Go**.
3. Upon success, the `Validation Status` will become `Valid`, and your first Team and Workspace will be automatically selected.

## 2. Syncing Courses
Before enrolling students, you must pull your products from ClickFunnels:
1. Navigate to the **Courses** section in the Django Admin.
2. Select any record (or all).
3. Choose the action **Sync Courses From ClickFunnels** and click **Go**.
4. This will populate local records with all courses from your active workspace.

## 3. Managing Enrollments

### Single Enrollment
Use the API `POST /api/enrollments/` or add a manual entry in the Admin if needed (though API/Bulk is preferred).

### Bulk Enrollment
1. Use the API `POST /api/enrollments/bulk`.
2. Provide a list of up to 50 emails and a `course_id`.
3. The system will synchronously attempt each enrollment and return a result for every email.

## 4. Reviewing Audit Logs
Navigate to **Enrollment Attempts** in the Admin.
- **Integrity:** Attempts are read-only.
- **Failures:** If an attempt status is `FAILURE`, open the record and inspect the `Error Log` and `Response Payload`.

## 5. Troubleshooting

### "401 Unauthorized" (API)
- Ensure you are logged into the Django Admin in the same browser.

### "403 Forbidden" (CF API)
- Ensure the `API User Agent` is set correctly and not blocked by CF security.

### "Invalid Token" (Encryption)
- If you lose your `FIELD_ENCRYPTION_KEY`, you must re-save your configurations in the Admin to re-encrypt the tokens.

### Logs
Search the `stdout` logs for `FAILURE` tags to see stack traces for unexpected errors.
