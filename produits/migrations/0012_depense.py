from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import decimal


class Migration(migrations.Migration):

    dependencies = [
        ("produits", "0011_vente_statut_paiement_date_reglement"),
    ]

    operations = [
        migrations.CreateModel(
            name="Depense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("type_depense", models.CharField(choices=[("investissement", "Depense d'investissement"), ("morte", "Depense morte")], max_length=20)),
                ("nature", models.CharField(max_length=255)),
                ("montant", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("date_depense", models.DateField(default=django.utils.timezone.now)),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="produits.user")),
            ],
        ),
    ]
