from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("produits", "0014_ransonjournalier"),
    ]

    operations = [
        migrations.CreateModel(
            name="CommandeInterne",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("numero", models.CharField(blank=True, default="", max_length=30, unique=True)),
                ("quantite", models.PositiveIntegerField(default=1)),
                ("message", models.TextField(blank=True, default="")),
                ("statut", models.CharField(choices=[("envoyee", "Envoyée au magasin"), ("expediee", "Expédiée par le magasin"), ("validee", "Réception validée par la boutique"), ("annulee", "Annulée")], default="envoyee", max_length=12)),
                ("bordereau_numero", models.CharField(blank=True, default="", max_length=50)),
                ("bordereau_image", models.ImageField(blank=True, null=True, upload_to="bordereaux_magasin/")),
                ("preuve_reception_image", models.ImageField(blank=True, null=True, upload_to="receptions_boutique/")),
                ("stock_avant_reception", models.IntegerField(default=0)),
                ("stock_apres_reception", models.IntegerField(default=0)),
                ("date_expedition", models.DateTimeField(blank=True, null=True)),
                ("date_validation", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("boutique_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="commandes_boutique_emises", to="produits.user")),
                ("magasinier_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="commandes_magasin_traitees", to="produits.user")),
                ("produit", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="commandes_internes", to="produits.produit")),
                ("validation_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="commandes_boutique_validees", to="produits.user")),
            ],
            options={"ordering": ("-created_at",)},
        ),
    ]
