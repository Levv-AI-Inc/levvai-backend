from django.urls import path

from .views import ActorsView, CollectionView, RowView, SeedTargetView, TxView

urlpatterns = [
    path("tx/", TxView.as_view(), name="flowstudio-tx"),
    path("actors/", ActorsView.as_view(), name="flowstudio-actors"),
    path("seed-target/", SeedTargetView.as_view(), name="flowstudio-seed-target"),
    path("c/<slug:collection>/", CollectionView.as_view(), name="flowstudio-collection"),
    path("c/<slug:collection>/<str:row_id>/", RowView.as_view(), name="flowstudio-row"),
]
