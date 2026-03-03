from django.contrib import admin

from .models import CommandeInterne, CommandeInterneAudit, StockMagasin


@admin.register(CommandeInterne)
class CommandeInterneAdmin(admin.ModelAdmin):
    list_display = ("numero", "qr_token", "produit", "quantite", "statut", "boutique_user", "magasinier_user", "created_at")
    list_filter = ("statut", "created_at")
    search_fields = ("numero", "produit__nom", "boutique_user__username", "magasinier_user__username")


@admin.register(CommandeInterneAudit)
class CommandeInterneAuditAdmin(admin.ModelAdmin):
    list_display = ("commande", "action", "espace", "acteur", "created_at")
    list_filter = ("action", "espace", "created_at")
    search_fields = ("commande__numero", "acteur__username", "details")


@admin.register(StockMagasin)
class StockMagasinAdmin(admin.ModelAdmin):
    list_display = ("produit", "quantite", "stock_min", "updated_at")
    search_fields = ("produit__nom",)
