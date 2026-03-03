from django.urls import path

from .views import audit_export_csv, audit_export_pdf, bordereau_pdf, dashboard_magasin, expedier_commande


urlpatterns = [
    path("", dashboard_magasin, name="magasin_dashboard"),
    path("commandes/<int:commande_id>/expedier/", expedier_commande, name="magasin_expedier_commande"),
    path("commandes/<int:commande_id>/bordereau.pdf", bordereau_pdf, name="magasin_bordereau_pdf"),
    path("audit/export/csv/", audit_export_csv, name="magasin_audit_export_csv"),
    path("audit/export/pdf/", audit_export_pdf, name="magasin_audit_export_pdf"),
]
