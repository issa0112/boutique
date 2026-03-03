from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("produits", "0009_proforma_full_sale_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="vente",
            name="mode_paiement",
            field=models.CharField(
                blank=True,
                choices=[
                    ("livraison", "Paiement a la livraison"),
                    ("cash", "Paiement en cash"),
                    ("cheque", "Paiement par cheque"),
                    ("mobile", "Orange Money"),
                    ("transaction_bancaire", "Transaction bancaire"),
                    ("remise", "Remise vendeur"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="vente",
            name="paiement_info_cle",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
