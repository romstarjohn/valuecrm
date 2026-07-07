# UI Architecture: ValuedCRM Portal

## Overview
ValuedCRM is a custom-built CRM portal designed for ClickFunnels managers. It moves beyond the Django Admin into a professional, user-centric interface inspired by ClickFunnels.

## Core Design Principles
1. **Focus on Action:** Primary actions (Enroll, Sync, Search) must be easily accessible.
2. **Visual Consistency:** Clean white cards, dark sidebar, and standardized typography (Inter).
3. **Audit Clarity:** Users should always know the status of an enrollment attempt without digging through logs.
4. **Performance:** Fast, responsive layouts using standard Django Template Language (DTL) and Bootstrap 5.

---

## Phase 2 Roadmap: The Custom Portal

### Step 1: Design System & Core Layout
*Establish the visual foundation used by all subsequent steps.*
- **Components:** Sidebar (Dark), Topbar (White), CRM Card, Status Badges, Page Header.
- **Templates:** `base.html`, `includes/sidebar.html`, `includes/topbar.html`.
- **Static Assets:** `static/css/app.css` (The Design System).
- **Acceptance Criteria:** A responsive layout with a functional sidebar and consistent typography.

### Step 2: Contacts CRM UI
*The hub for student management.*
- **URLs:** `/contacts/`, `/contacts/add/`, `/contacts/<id>/`.
- **Views:** `ContactListView`, `ContactCreateView`, `ContactDetailView`.
- **Forms:** `ContactForm` (ModelForm).
- **Templates:** `contacts/list.html`, `contacts/detail.html`, `contacts/form.html`.
- **Reusable Components:** `table_pagination.html`, `search_bar.html`.
- **Acceptance Criteria:** Users can browse, search, and view detailed student info with enrollment history.

### Step 3: Courses CRM UI
*Manage the integration catalog.*
- **URLs:** `/courses/`, `/courses/sync/`.
- **Views:** `CourseListView`, `CourseSyncView`.
- **Forms:** `SyncRequestForm`.
- **Templates:** `courses/list.html`, `courses/sync_confirm.html`.
- **Acceptance Criteria:** Users can see synced ClickFunnels products and trigger a manual sync via the UI.

### Step 4: Enrollment Center UI
*The core business engine.*
- **URLs:** `/enrollments/`, `/enrollments/bulk/`.
- **Views:** `EnrollmentListView`, `BulkEnrollmentView`.
- **Forms:** `BulkEnrollmentForm` (Textarea for emails + Course selection).
- **Templates:** `enrollments/list.html`, `enrollments/bulk_form.html`.
- **Acceptance Criteria:** Users can trigger bulk enrollments and see a live result list (Success/Failure) for each entry.

### Step 5: Configuration UI
*Self-service setup for the ClickFunnels bridge.*
- **URLs:** `/settings/`.
- **Views:** `SettingsView`, `VerifyConnectionView`.
- **Forms:** `ClickFunnelsConfigForm` (API Token masking).
- **Templates:** `configuration/settings.html`.
- **Acceptance Criteria:** Users can update their CFv2 token and verify the connection without entering the Django Admin.

### Step 6: Dashboard Upgrade
*Visual analytics for the home screen.*
- **URLs:** `/`.
- **Views:** `DashboardView`.
- **Templates:** `dashboard/index.html`.
- **Components:** `metric_card.html`, `activity_feed.html`.
- **Acceptance Criteria:** A "at-a-glance" view of enrollment trends and recent failures.

### Step 7: Final UI Polish
*Animations, transitions, and error states.*
- **Focus:** `Empty States`, `Loading Spinners`, `Form Validation UI`, `Toast Notifications`.
- **Acceptance Criteria:** The app feels fluid and professional; errors are presented gracefully to the user.

---

## Technical Implementation Notes
- **Authentication:** All views must use `LoginRequiredMixin`.
- **Context Processors:** Use a custom processor to inject the active ClickFunnels status into the topbar.
- **CSRF:** Standard Django CSRF protection on all forms.
- **Messages:** Extensive use of `django.contrib.messages` for success/error feedback.
