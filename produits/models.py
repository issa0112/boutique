from django.db import models
from django.utils import timezone
from django.contrib.auth.models import AbstractUser, Group, Permission
from decimal import Decimal

from django.db import models
import qrcode
from io import BytesIO
from django.core.files import File


class User(AbstractUser):
    ROLE_CHOICES = (
        ("admin", "Admin"),
        ("gerant", "Gérant"),
        ("caissier", "Caissier"),
        ("comptable", "Comptable"),
        ("magasinier", "Magasinier"),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="caissier")

    # Corriger le conflit avec auth.User
    groups = models.ManyToManyField(
        Group,
        related_name="produits_user_set",  # évite le clash avec auth.User
        blank=True,
        help_text="The groups this user belongs to.",
        verbose_name="groups"
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name="produits_user_permissions_set",  # évite le clash
        blank=True,
        help_text="Specific permissions for this user.",
        verbose_name="user permissions"
    )

    def __str__(self):
        return self.username

# 2️⃣ Fournisseurs
# -----------------------------
class Fournisseur(models.Model):
    nom = models.CharField(max_length=100)
    contact = models.CharField(max_length=50)
    email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return self.nom


class Category(models.Model):
    nom = models.CharField(max_length=100)

    def __str__(self):
        return self.nom


# 3️⃣ Produits et QR codes
# -----------------------------

class Produit(models.Model):
    nom = models.CharField(max_length=255)
    prix = models.DecimalField(max_digits=10, decimal_places=2)
    quantite = models.IntegerField()
    quantite_defectueuse = models.IntegerField(default=0)
    quantite_reparation = models.IntegerField(default=0)
    stock_min = models.IntegerField(default=0)
    fournisseur = models.ForeignKey(Fournisseur, on_delete=models.SET_NULL, null=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, default=1)
    date_ajout = models.DateTimeField(default=timezone.now)
    date_expiration = models.DateField(null=True, blank=True)
    image = models.ImageField(null=True, blank=True, upload_to='media/')
    qr_code = models.ImageField(upload_to="qr_codes/", null=True, blank=True)

    def __str__(self):
        return self.nom


# 4️⃣ Clients pour fidélité
# -----------------------------
# models.py



class Client(models.Model):
    nom = models.CharField(max_length=255)
    telephone = models.CharField(max_length=20, default="00000000")
    points_fidelite = models.IntegerField(default=0)
    email = models.EmailField(blank=True, null=True) 
    qr_code = models.ImageField(upload_to="qr_codes/", blank=True, null=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None  # Vérifie si c'est un nouvel enregistrement
        super().save(*args, **kwargs)  # Sauvegarde initiale pour obtenir un ID

        # Génération du QR code seulement si c'est nouveau et pas déjà présent
        if is_new and not self.qr_code:
            qr = qrcode.QRCode(box_size=2, border=1)
            qr.add_data(f"Client:{self.id} - {self.nom}")
            qr.make(fit=True)
            img = qr.make_image()
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            self.qr_code.save(f"client_{self.id}.png", File(buffer), save=False)
            self.save(update_fields=['qr_code'])  # Met à jour uniquement le champ QR code

    def __str__(self):
        return self.nom

from django.conf import settings

class Vente(models.Model):
    PAIEMENT_CHOICES = (
        ("livraison", "Paiement a la livraison"),
        ("cash", "Paiement en cash"),
        ("cheque", "Paiement par cheque"),
        ("mobile", "Orange Money"),
        ("transaction_bancaire", "Transaction bancaire"),
        ("remise", "Remise vendeur"),
    )
    STATUT_PAIEMENT_CHOICES = (
        ("impaye", "Impayé"),
        ("paye", "Payé"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        null=True, blank=True  # ← mets ça si tu veux que ce soit optionnel
    )
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True)
    date_vente = models.DateTimeField(auto_now_add=True)
    cheque_image = models.ImageField(upload_to='cheques/', null=True, blank=True)  # nouveau champ
    mode_paiement = models.CharField(max_length=30, choices=PAIEMENT_CHOICES, blank=True, default="")
    paiement_info_cle = models.CharField(max_length=255, blank=True, default="")
    statut_paiement = models.CharField(max_length=10, choices=STATUT_PAIEMENT_CHOICES, default="impaye")
    date_reglement = models.DateTimeField(null=True, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_before_discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_type = models.CharField(max_length=10, blank=True, default="")
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    coupon_code = models.CharField(max_length=50, blank=True, default="")
    coupon_discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    commission = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    
    def __str__(self):
        return f"Vente #{self.id} - {self.date_vente.strftime('%d/%m/%Y %H:%M')}"

class LigneVente(models.Model):
    vente = models.ForeignKey(Vente, on_delete=models.CASCADE, related_name="lignes")
    produit = models.ForeignKey('Produit', on_delete=models.SET_NULL, null=True)
    quantite = models.IntegerField()
    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2)
    remise_type = models.CharField(max_length=10, blank=True, default="")
    remise_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_ligne = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"{self.produit.nom} x {self.quantite}"

class Personnel(models.Model):
    STATUT_CHOICES = (
        ("actif", "Actif"),
        ("inactif", "Inactif"),
    )
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100, blank=True)
    telephone = models.CharField(max_length=30)
    email = models.EmailField(blank=True, null=True)
    poste = models.CharField(max_length=100)
    date_embauche = models.DateField(default=timezone.now)
    salaire_base = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default="actif")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.prenom} {self.nom}".strip()


class Paie(models.Model):
    personnel = models.ForeignKey(Personnel, on_delete=models.CASCADE, related_name="paies")
    periode = models.DateField()
    heures = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    taux_horaire = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    prime = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    avance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    retenue = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    salaire_base = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)

    def calcul_total(self):
        base = self.salaire_base
        if base <= 0 and self.heures > 0 and self.taux_horaire > 0:
            base = self.heures * self.taux_horaire
        return base + self.prime - self.avance - self.retenue

    def save(self, *args, **kwargs):
        self.total = self.calcul_total()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Paie {self.personnel} - {self.periode}"


class AvanceSalaire(models.Model):
    STATUT_CHOICES = (
        ("ouverte", "Ouverte"),
        ("soldee", "Soldée"),
    )
    personnel = models.ForeignKey(Personnel, on_delete=models.CASCADE, related_name="avances_salaire")
    montant = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    date_avance = models.DateField(default=timezone.now)
    note = models.TextField(blank=True, default="")
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default="ouverte")
    paie = models.ForeignKey("Paie", on_delete=models.SET_NULL, null=True, blank=True, related_name="avances_soldees")
    date_solde = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Avance {self.personnel} - {self.montant}"


class RansonJournalier(models.Model):
    personnel = models.ForeignKey(Personnel, on_delete=models.CASCADE, related_name="ransons_journalieres")
    date_jour = models.DateField(default=timezone.now)
    present = models.BooleanField(default=False)
    montant = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    est_paye = models.BooleanField(default=False)
    date_paiement = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default="")
    controle_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["personnel", "date_jour"], name="uniq_ranson_personnel_jour"),
        ]

    def __str__(self):
        return f"Rançon {self.personnel} - {self.date_jour}"


class StockMovement(models.Model):
    TYPE_CHOICES = (
        ("entree", "Entrée"),
        ("sortie", "Sortie"),
        ("ajustement", "Ajustement"),
    )
    produit = models.ForeignKey(Produit, on_delete=models.CASCADE, related_name="mouvements_stock")
    type_mouvement = models.CharField(max_length=12, choices=TYPE_CHOICES)
    quantite = models.IntegerField()
    stock_avant = models.IntegerField()
    stock_apres = models.IntegerField()
    note = models.CharField(max_length=255, blank=True)
    vente = models.ForeignKey(Vente, on_delete=models.SET_NULL, null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.produit.nom} - {self.type_mouvement} ({self.quantite})"


class SuiviProduitEtat(models.Model):
    ETAT_CHOICES = (
        ("disponible", "Disponible"),
        ("defectueux", "Défectueux"),
        ("reparation", "En réparation"),
        ("rebut", "Rebut"),
    )
    ACTION_CHOICES = (
        ("classification", "Classement défectueux"),
        ("envoi_reparation", "Envoi réparation"),
        ("retour_reparation", "Retour réparation"),
        ("rebut", "Mise au rebut"),
    )
    produit = models.ForeignKey(Produit, on_delete=models.CASCADE, related_name="suivis_etat")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    etat_apres = models.CharField(max_length=20, choices=ETAT_CHOICES)
    quantite = models.PositiveIntegerField(default=0)
    reference = models.CharField(max_length=100, blank=True, default="")
    cout_reparation = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    date_prevue_retour = models.DateField(null=True, blank=True)
    date_effective = models.DateField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.produit.nom} - {self.get_action_display()} ({self.quantite})"


# 6️⃣ Alertes et notifications
# -----------------------------
class Alerte(models.Model):
    TYPE_CHOICES = (
        ("stock", "Stock faible"),
        ("expiration", "Produit expiré"),
        ("vente", "Vente anormale"),
    )
    type_alerte = models.CharField(max_length=20, choices=TYPE_CHOICES)
    produit = models.ForeignKey(Produit, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateTimeField(default=timezone.now)
    lue = models.BooleanField(default=False)


    # 7️⃣ Historique des analyses prédictives
# -----------------------------
class AnalysePred(models.Model):
    produit = models.ForeignKey(Produit, on_delete=models.CASCADE)
    prediction_vente = models.PositiveIntegerField()
    semaine = models.DateField()


class Depense(models.Model):
    TYPE_CHOICES = (
        ("investissement", "Depense d'investissement"),
        ("morte", "Depense morte"),
    )
    type_depense = models.CharField(max_length=20, choices=TYPE_CHOICES)
    nature = models.CharField(max_length=255)
    montant = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    date_depense = models.DateField(default=timezone.now)
    note = models.TextField(blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_type_depense_display()} - {self.nature}"


class BonAchat(models.Model):
    TYPE_CHOICES = (
        ("percent", "Pourcentage"),
        ("amount", "Montant"),
    )
    code = models.CharField(max_length=50, unique=True)
    type_remise = models.CharField(max_length=10, choices=TYPE_CHOICES)
    valeur = models.DecimalField(max_digits=12, decimal_places=2)
    actif = models.BooleanField(default=True)
    date_expiration = models.DateField(null=True, blank=True)
    usages_max = models.IntegerField(default=0)
    usages = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code


class BonCommande(models.Model):
    STATUT_CHOICES = (
        ("brouillon", "Brouillon"),
        ("valide", "Validé"),
        ("livre", "Livré"),
        ("annule", "Annulé"),
    )
    fournisseur = models.ForeignKey(Fournisseur, on_delete=models.SET_NULL, null=True, blank=True)
    produit = models.ForeignKey(Produit, on_delete=models.SET_NULL, null=True, blank=True)
    quantite = models.PositiveIntegerField(default=1)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    statut = models.CharField(max_length=12, choices=STATUT_CHOICES, default="brouillon")
    note = models.TextField(blank=True, default="")
    date_commande = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.total = Decimal(self.quantite) * self.prix_unitaire
        super().save(*args, **kwargs)

    def __str__(self):
        return f"BC #{self.id}"


class FactureProforma(models.Model):
    STATUT_CHOICES = (
        ("brouillon", "Brouillon"),
        ("envoyee", "Envoyée"),
        ("acceptee", "Acceptée"),
        ("expiree", "Expirée"),
    )
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True)
    produit = models.ForeignKey(Produit, on_delete=models.SET_NULL, null=True, blank=True)
    quantite = models.PositiveIntegerField(default=1)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    remise = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_before_discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_type = models.CharField(max_length=10, blank=True, default="")
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    coupon_code = models.CharField(max_length=50, blank=True, default="")
    coupon_discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    commission = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    statut = models.CharField(max_length=12, choices=STATUT_CHOICES, default="brouillon")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_expiration = models.DateField(null=True, blank=True)
    vente_convertie = models.ForeignKey("Vente", on_delete=models.SET_NULL, null=True, blank=True, related_name="proformas_converties")
    date_conversion = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    def save(self, *args, **kwargs):
        if self.total_before_discount > 0 or self.total_discount > 0:
            self.total = self.total_before_discount - self.total_discount
        else:
            base_total = Decimal(self.quantite) * self.prix_unitaire
            self.total = base_total - self.remise
        if self.total < 0:
            self.total = Decimal("0.00")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Proforma #{self.id}"


class LigneProforma(models.Model):
    proforma = models.ForeignKey(FactureProforma, on_delete=models.CASCADE, related_name="lignes")
    produit = models.ForeignKey(Produit, on_delete=models.SET_NULL, null=True)
    quantite = models.IntegerField()
    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2)
    remise_type = models.CharField(max_length=10, blank=True, default="")
    remise_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_ligne = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        produit_nom = self.produit.nom if self.produit else "Produit supprimé"
        return f"{produit_nom} x {self.quantite}"


