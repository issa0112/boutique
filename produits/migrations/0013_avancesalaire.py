from django.db import migrations, models
import decimal
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("produits", "0012_depense"),
    ]

    operations = [
        migrations.CreateModel(
            name="AvanceSalaire",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("montant", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("date_avance", models.DateField(default=django.utils.timezone.now)),
                ("note", models.TextField(blank=True, default="")),
                ("statut", models.CharField(choices=[("ouverte", "Ouverte"), ("soldee", "Soldée")], default="ouverte", max_length=10)),
                ("date_solde", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("paie", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="avances_soldees", to="produits.paie")),
                ("personnel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="avances_salaire", to="produits.personnel")),
            ],
        ),
    ]
