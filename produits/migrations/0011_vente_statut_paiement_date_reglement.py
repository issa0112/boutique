from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produits", "0010_vente_mode_paiement_and_info_cle"),
    ]

    operations = [
        migrations.AddField(
            model_name="vente",
            name="date_reglement",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vente",
            name="statut_paiement",
            field=models.CharField(
                choices=[("impaye", "Impayé"), ("paye", "Payé")],
                default="impaye",
                max_length=10,
            ),
        ),
    ]
