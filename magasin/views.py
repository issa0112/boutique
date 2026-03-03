from io import BytesIO

import qrcode
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import CommandeInterne, CommandeInterneAudit, StockMagasin
from produits.models import Produit


def _audit_commande(commande, action, espace, acteur=None, details=""):
    CommandeInterneAudit.objects.create(
        commande=commande,
        action=action,
        espace=espace,
        acteur=acteur if getattr(acteur, "is_authenticated", False) else None,
        details=details or "",
    )


def _build_bordereau_pdf_bytes(commande):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph("<b>Bordereau de commande interne</b>", styles["Title"]))
    elements.append(Paragraph(f"N° commande : {commande.numero}", styles["Normal"]))
    elements.append(Paragraph(f"N° bordereau : {commande.bordereau_numero}", styles["Normal"]))
    elements.append(
        Paragraph(
            f"Date expédition : {commande.date_expedition.strftime('%d/%m/%Y %H:%M') if commande.date_expedition else '-'}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 10))

    data = [["Produit", "Quantité", "Prix unitaire", "Montant estimé"]]
    data.append(
        [
            commande.produit.nom if commande.produit else "N/A",
            str(commande.quantite),
            f"{commande.produit.prix if commande.produit else 0} FCFA",
            f"{commande.montant_estime()} FCFA",
        ]
    )
    table = Table(data, colWidths=[70 * mm, 25 * mm, 40 * mm, 40 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 14))
    magasinier = commande.magasinier_user.username if commande.magasinier_user else "N/A"
    boutique = commande.boutique_user.username if commande.boutique_user else "N/A"
    elements.append(Paragraph(f"Demandeur boutique : {boutique}", styles["Normal"]))
    elements.append(Paragraph(f"Magasinier expéditeur : {magasinier}", styles["Normal"]))
    if commande.message:
        elements.append(Paragraph(f"Message commande : {commande.message}", styles["Normal"]))

    qr_payload = f"COMMANDE:{commande.numero}|BORDEREAU:{commande.bordereau_numero}|TOKEN:{commande.qr_token}"
    qr = qrcode.QRCode(box_size=2, border=1)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    qr_img = qr.make_image()
    qr_buffer = BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("QR bordereau (scan à la réception boutique)", styles["Normal"]))
    elements.append(RLImage(qr_buffer, width=30 * mm, height=30 * mm))

    doc.build(elements)
    return buffer.getvalue()


def dashboard_magasin(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "stock_update":
            produit_id = request.POST.get("produit_id")
            try:
                quantite = int(request.POST.get("quantite") or 0)
                stock_min = int(request.POST.get("stock_min") or 0)
            except ValueError:
                messages.error(request, "Quantité ou stock min invalide.")
                return redirect("magasin_dashboard")
            if not produit_id:
                messages.error(request, "Produit requis pour le stock magasin.")
                return redirect("magasin_dashboard")

            stock_obj, _ = StockMagasin.objects.get_or_create(
                produit_id=produit_id,
                defaults={"quantite": 0, "stock_min": 0},
            )
            stock_obj.quantite = quantite
            stock_obj.stock_min = stock_min
            stock_obj.save(update_fields=["quantite", "stock_min", "updated_at"])
            messages.success(request, "Stock magasin mis à jour.")
            return redirect("magasin_dashboard")

    commandes = CommandeInterne.objects.select_related("produit", "boutique_user", "magasinier_user").all()
    audits = CommandeInterneAudit.objects.select_related("commande", "acteur")
    stocks = StockMagasin.objects.select_related("produit").all()
    produits = Produit.objects.order_by("nom")
    statut_filter = (request.GET.get("statut") or "").strip()
    if statut_filter:
        commandes = commandes.filter(statut=statut_filter)

    pending_count = commandes.filter(statut="envoyee").count()
    expediees_count = commandes.filter(statut="expediee").count()

    return render(
        request,
        "magasin/dashboard.html",
        {
            "commandes": commandes[:300],
            "audits": audits[:200],
            "stocks": stocks[:300],
            "produits": produits,
            "statut_filter": statut_filter,
            "pending_count": pending_count,
            "expediees_count": expediees_count,
        },
    )


def expedier_commande(request, commande_id):
    if request.method != "POST":
        return redirect("magasin_dashboard")

    commande = get_object_or_404(CommandeInterne, id=commande_id)
    if commande.statut != "envoyee":
        messages.error(request, "Cette commande ne peut plus être expédiée.")
        return redirect("magasin_dashboard")

    bordereau_numero = (request.POST.get("bordereau_numero") or "").strip()
    if not bordereau_numero:
        bordereau_numero = f"BRD-{timezone.localdate().strftime('%Y%m%d')}-{commande.id:05d}"

    with transaction.atomic():
        stock_obj, _ = StockMagasin.objects.select_for_update().get_or_create(
            produit=commande.produit,
            defaults={"quantite": 0, "stock_min": 0},
        )
        if commande.quantite > stock_obj.quantite:
            messages.error(
                request,
                f"Stock magasin insuffisant pour {commande.produit.nom}. Disponible: {stock_obj.quantite}, demandé: {commande.quantite}.",
            )
            return redirect("magasin_dashboard")

        stock_obj.quantite -= commande.quantite
        stock_obj.save(update_fields=["quantite", "updated_at"])

        commande.statut = "expediee"
        commande.bordereau_numero = bordereau_numero
        commande.date_expedition = timezone.now()
        commande.magasinier_user = request.user if request.user.is_authenticated else None
        if request.FILES.get("bordereau_image"):
            commande.bordereau_image = request.FILES["bordereau_image"]
        pdf_bytes = _build_bordereau_pdf_bytes(commande)
        commande.bordereau_pdf.save(f"bordereau_{commande.numero}.pdf", ContentFile(pdf_bytes), save=False)
        commande.save(
            update_fields=[
                "statut",
                "bordereau_numero",
                "date_expedition",
                "magasinier_user",
                "bordereau_image",
                "bordereau_pdf",
                "updated_at",
            ]
        )

    _audit_commande(
        commande=commande,
        action="expedition",
        espace="magasin",
        acteur=request.user,
        details=f"Expédition validée. Bordereau: {commande.bordereau_numero}. Stock magasin restant: {stock_obj.quantite}.",
    )
    messages.success(request, f"Commande {commande.numero} expédiée avec bordereau {commande.bordereau_numero}.")
    return redirect("magasin_dashboard")


def bordereau_pdf(request, commande_id):
    commande = get_object_or_404(CommandeInterne, id=commande_id)
    if commande.bordereau_pdf:
        return redirect(commande.bordereau_pdf.url)

    if not commande.bordereau_numero:
        commande.bordereau_numero = f"BRD-{timezone.localdate().strftime('%Y%m%d')}-{commande.id:05d}"
    pdf_bytes = _build_bordereau_pdf_bytes(commande)
    commande.bordereau_pdf.save(f"bordereau_{commande.numero}.pdf", ContentFile(pdf_bytes), save=False)
    commande.save(update_fields=["bordereau_numero", "bordereau_pdf", "updated_at"])
    return redirect(commande.bordereau_pdf.url)


def audit_export_csv(request):
    audits = CommandeInterneAudit.objects.select_related("commande", "acteur")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="audit_commandes_internes.csv"'
    import csv

    writer = csv.writer(response)
    writer.writerow(["Date", "Commande", "Action", "Espace", "Acteur", "Details"])
    for a in audits:
        writer.writerow(
            [
                a.created_at.strftime("%d/%m/%Y %H:%M"),
                a.commande.numero if a.commande else "N/A",
                a.get_action_display(),
                a.get_espace_display(),
                a.acteur.username if a.acteur else "N/A",
                a.details,
            ]
        )
    return response


def audit_export_pdf(request):
    audits = CommandeInterneAudit.objects.select_related("commande", "acteur")[:300]
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="audit_commandes_internes.pdf"'
    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )
    styles = getSampleStyleSheet()
    elements = [Paragraph("<b>Journal d'audit commandes internes</b>", styles["Title"]), Spacer(1, 10)]
    data = [["Date", "Commande", "Action", "Espace", "Acteur", "Détails"]]
    for a in audits:
        data.append(
            [
                a.created_at.strftime("%d/%m/%Y %H:%M"),
                a.commande.numero if a.commande else "N/A",
                a.get_action_display(),
                a.get_espace_display(),
                a.acteur.username if a.acteur else "N/A",
                (a.details or "")[:80],
            ]
        )
    table = Table(data, colWidths=[30 * mm, 28 * mm, 25 * mm, 20 * mm, 22 * mm, 55 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    return response

