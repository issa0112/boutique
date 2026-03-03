from django.db import migrations, models
import decimal
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("produits", "0013_avancesalaire"),
    ]

    operations = [
        migrations.CreateModel(
            name="RansonJournalier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date_jour", models.DateField(default=django.utils.timezone.now)),
                ("present", models.BooleanField(default=False)),
                ("montant", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("est_paye", models.BooleanField(default=False)),
                ("date_paiement", models.DateTimeField(blank=True, null=True)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("controle_par", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="produits.user")),
                ("personnel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ransons_journalieres", to="produits.personnel")),
            ],
        ),
        migrations.AddConstraint(
            model_name="ransonjournalier",
            constraint=models.UniqueConstraint(fields=("personnel", "date_jour"), name="uniq_ranson_personnel_jour"),
        ),
    ]
