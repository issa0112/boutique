from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone

from produits.models import Produit


class CommandeInterne(models.Model):
    STATUT_CHOICES = (
        ("envoyee", "Envoyée au magasin"),
        ("expediee", "Expédiée par le magasin"),
        ("validee", "Réception validée par la boutique"),
        ("annulee", "Annulée"),
    )

    numero = models.CharField(max_length=30, unique=True, blank=True, default="")
    qr_token = models.CharField(max_length=40, unique=True, blank=True, default="")
    produit = models.ForeignKey(Produit, on_delete=models.PROTECT, related_name="commandes_internes")
    quantite = models.PositiveIntegerField(default=1)
    message = models.TextField(blank=True, default="")

    boutique_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commandes_boutique_emises",
    )
    magasinier_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commandes_magasin_traitees",
    )
    validation_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commandes_boutique_validees",
    )

    statut = models.CharField(max_length=12, choices=STATUT_CHOICES, default="envoyee")
    bordereau_numero = models.CharField(max_length=50, blank=True, default="")
    bordereau_image = models.ImageField(upload_to="bordereaux_magasin/", null=True, blank=True)
    bordereau_pdf = models.FileField(upload_to="bordereaux_magasin/", null=True, blank=True)
    preuve_reception_image = models.ImageField(upload_to="receptions_boutique/", null=True, blank=True)

    stock_avant_reception = models.IntegerField(default=0)
    stock_apres_reception = models.IntegerField(default=0)
    date_expedition = models.DateTimeField(null=True, blank=True)
    date_validation = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.numero or f"CMD-{self.id}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        updates = []
        if is_new and not self.numero:
            self.numero = f"CMD-{timezone.localdate().strftime('%Y%m%d')}-{self.id:05d}"
            updates.append("numero")
        if not self.qr_token:
            self.qr_token = uuid4().hex.upper()
            updates.append("qr_token")
        if updates:
            super().save(update_fields=updates)

    def montant_estime(self):
        return (self.produit.prix or Decimal("0.00")) * Decimal(self.quantite or 0)


class CommandeInterneAudit(models.Model):
    ACTION_CHOICES = (
        ("emission", "Emission boutique"),
        ("expedition", "Expedition magasin"),
        ("validation", "Validation reception boutique"),
        ("annulation", "Annulation"),
    )
    ESPACE_CHOICES = (
        ("boutique", "Boutique"),
        ("magasin", "Magasin"),
        ("systeme", "Système"),
    )

    commande = models.ForeignKey(CommandeInterne, on_delete=models.CASCADE, related_name="audits")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    espace = models.CharField(max_length=10, choices=ESPACE_CHOICES, default="systeme")
    acteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_commandes_internes",
    )
    details = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.commande.numero} - {self.action}"


class StockMagasin(models.Model):
    produit = models.OneToOneField(Produit, on_delete=models.CASCADE, related_name="stock_magasin")
    quantite = models.IntegerField(default=0)
    stock_min = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("produit__nom",)

    def __str__(self):
        return f"{self.produit.nom} - {self.quantite}"
