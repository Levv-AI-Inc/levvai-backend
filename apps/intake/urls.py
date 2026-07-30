from django.urls import path

from apps.intake.views import (
    ApprovalsDashboardView,
    CandidateDirectoryDetailView,
    CandidateDirectoryView,
    IntakeApprovalPreviewView,
    IntakeApproveView,
    IntakeDetailView,
    IntakeDraftCreateView,
    IntakeListView,
    IntakeRejectView,
    IntakeSelectedCandidateView,
    IntakeSubmitView,
    NovaIntakeConfidenceView,
)

urlpatterns = [
    path("approvals/dashboard", ApprovalsDashboardView.as_view(), name="approvals-dashboard"),
    path("api/approvals/dashboard", ApprovalsDashboardView.as_view(), name="api-approvals-dashboard"),
    path("candidates", CandidateDirectoryView.as_view(), name="candidate-directory"),
    path("api/candidates", CandidateDirectoryView.as_view(), name="api-candidate-directory"),
    path(
        "candidates/<int:candidate_id>",
        CandidateDirectoryDetailView.as_view(),
        name="candidate-directory-detail",
    ),
    path(
        "api/candidates/<int:candidate_id>",
        CandidateDirectoryDetailView.as_view(),
        name="api-candidate-directory-detail",
    ),
    path("intake", IntakeListView.as_view(), name="intake-list"),
    path("api/intake", IntakeListView.as_view(), name="api-intake-list"),
    path("intake/draft", IntakeDraftCreateView.as_view(), name="intake-draft-create"),
    path("api/intake/draft", IntakeDraftCreateView.as_view(), name="api-intake-draft-create"),
    path("intake/<int:intake_id>", IntakeDetailView.as_view(), name="intake-detail"),
    path("api/intake/<int:intake_id>", IntakeDetailView.as_view(), name="api-intake-detail"),
    path("intake/<int:intake_id>/submit", IntakeSubmitView.as_view(), name="intake-submit"),
    path("api/intake/<int:intake_id>/submit", IntakeSubmitView.as_view(), name="api-intake-submit"),
    path("intake/<int:intake_id>/approve", IntakeApproveView.as_view(), name="intake-approve"),
    path("api/intake/<int:intake_id>/approve", IntakeApproveView.as_view(), name="api-intake-approve"),
    path("intake/<int:intake_id>/reject", IntakeRejectView.as_view(), name="intake-reject"),
    path("api/intake/<int:intake_id>/reject", IntakeRejectView.as_view(), name="api-intake-reject"),
    path(
        "intake/<int:intake_id>/selected-candidates",
        IntakeSelectedCandidateView.as_view(),
        name="intake-selected-candidates",
    ),
    path(
        "api/intake/<int:intake_id>/selected-candidates",
        IntakeSelectedCandidateView.as_view(),
        name="api-intake-selected-candidates",
    ),
    path(
        "intake/<int:intake_id>/approval-preview",
        IntakeApprovalPreviewView.as_view(),
        name="intake-approval-preview",
    ),
    path(
        "api/intake/<int:intake_id>/approval-preview",
        IntakeApprovalPreviewView.as_view(),
        name="api-intake-approval-preview",
    ),
    path(
        "nova/intake/confidence",
        NovaIntakeConfidenceView.as_view(),
        name="nova-intake-confidence",
    ),
    path(
        "api/nova/intake/confidence",
        NovaIntakeConfidenceView.as_view(),
        name="api-nova-intake-confidence",
    ),
]
