from django.contrib import admin
from .models import (
    Produit, Client, Fournisseur, Category, Vente, LigneVente, Alerte,
    AnalysePred, Personnel, Paie, StockMovement, BonAchat, BonCommande, FactureProforma, LigneProforma
)

admin.site.register(Produit)
admin.site.register(Client)
admin.site.register(Fournisseur)
admin.site.register(Category)
admin.site.register(Vente)
admin.site.register(LigneVente)
admin.site.register(Alerte)
admin.site.register(AnalysePred)
admin.site.register(Personnel)
admin.site.register(Paie)
admin.site.register(StockMovement)
admin.site.register(BonAchat)
admin.site.register(BonCommande)
admin.site.register(FactureProforma)
admin.site.register(LigneProforma)
