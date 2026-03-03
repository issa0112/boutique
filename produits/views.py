from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from decimal import Decimal, InvalidOperation
from datetime import timedelta
import json
import csv
from io import BytesIO

import qrcode
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from django.template.loader import render_to_string
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Q, Count
from django.db.models.functions import TruncDate
from django.contrib.auth import get_user_model
from django.views.decorators.csrf import csrf_exempt
from django.core.files import File
from django.core.files.base import ContentFile
from django.urls import reverse
from urllib.parse import urlencode

from .models import (
    Produit, Fournisseur, Category, Client, Vente, LigneVente,
    Alerte, Personnel, Paie, AvanceSalaire, RansonJournalier, StockMovement, SuiviProduitEtat, BonAchat, BonCommande, FactureProforma, LigneProforma, Depense
)
from magasin.models import CommandeInterne, CommandeInterneAudit, StockMagasin

User = get_user_model()


# -----------------------------
# Helpers
# -----------------------------

def _get_user_or_none(request):
    if hasattr(request, "user") and request.user.is_authenticated:
        return request.user
    return None


def _ensure_stock_alert(produit):
    if produit.stock_min > 0 and produit.quantite <= produit.stock_min:
        if not Alerte.objects.filter(type_alerte="stock", produit=produit, lue=False).exists():
            Alerte.objects.create(type_alerte="stock", produit=produit)


def _create_stock_movement(produit, type_mouvement, quantite, stock_avant, stock_apres, user=None, note="", vente=None):
    StockMovement.objects.create(
        produit=produit,
        type_mouvement=type_mouvement,
        quantite=quantite,
        stock_avant=stock_avant,
        stock_apres=stock_apres,
        note=note,
        user=user,
        vente=vente,
    )


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


def _recalc_panier(panier):
    subtotal = Decimal("0.00")
    subtotal_initial = Decimal("0.00")
    total_remises = Decimal("0.00")
    total_marge = Decimal("0.00")
    for item in panier.values():
        prix_initial = Decimal(str(item.get("prix_initial") or item["prix"]))
        prix = Decimal(str(item["prix"]))
        if prix < prix_initial:
            prix = prix_initial
            item["prix"] = float(prix_initial)
        quantite = Decimal(str(item["quantite"]))
        line_sub = prix * quantite
        line_sub_initial = prix_initial * quantite
        remise_type = item.get("remise_type") or ""
        remise_value = Decimal(str(item.get("remise_value") or 0))
        remise_line = Decimal("0.00")
        if remise_type == "percent":
            remise_line = line_sub * remise_value / Decimal("100")
        elif remise_type == "amount":
            remise_line = remise_value
        if remise_line < 0:
            remise_line = Decimal("0.00")
        max_remise = line_sub - line_sub_initial
        if max_remise < 0:
            max_remise = Decimal("0.00")
        if remise_line > max_remise:
            remise_line = max_remise
        total_line = line_sub - remise_line
        item["total"] = float(total_line)
        item["remise_line"] = float(remise_line)
        item["prix_initial"] = float(prix_initial)
        subtotal += line_sub
        subtotal_initial += line_sub_initial
        total_remises += remise_line
        total_marge += total_line - line_sub_initial
    total_after_line = subtotal - total_remises
    if total_after_line < 0:
        total_after_line = Decimal("0.00")
    return {
        "subtotal": subtotal,
        "subtotal_initial": subtotal_initial,
        "total_remises": total_remises,
        "total_after_line": total_after_line,
        "total_marge": total_marge,
    }


# -----------------------------
# Dashboard
# -----------------------------

def dashboard(request):
    produits = Produit.objects.all()
    ventes = Vente.objects.order_by("-date_vente")[:10]
    alertes = Alerte.objects.filter(lue=False)
    total_ventes = Vente.objects.aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    stock_bas = Produit.objects.filter(stock_min__gt=0, quantite__lte=F("stock_min")).count()
    total_produits = Produit.objects.count()
    total_clients = Client.objects.count()
    total_personnel = Personnel.objects.count()
    total_ventes_count = Vente.objects.count()
    total_articles_vendus = LigneVente.objects.aggregate(qte=Sum("quantite")).get("qte") or 0
    ticket_moyen = total_ventes / total_ventes_count if total_ventes_count else Decimal("0.00")
    total_commissions = Vente.objects.aggregate(total=Sum("commission")).get("total") or Decimal("0.00")

    ca_expr = ExpressionWrapper(F("quantite") * F("prix_unitaire"), output_field=DecimalField(max_digits=12, decimal_places=2))
    top_produits = (
        LigneVente.objects.values("produit__nom")
        .annotate(qte=Sum("quantite"), ca=Sum(ca_expr))
        .order_by("-ca")[:5]
    )
    return render(request, "boutique/dashboard.html", {
        "produits": produits,
        "ventes": ventes,
        "alertes": alertes,
        "total_ventes": total_ventes,
        "stock_bas": stock_bas,
        "total_produits": total_produits,
        "total_clients": total_clients,
        "total_personnel": total_personnel,
        "total_ventes_count": total_ventes_count,
        "total_articles_vendus": total_articles_vendus,
        "ticket_moyen": ticket_moyen,
        "total_commissions": total_commissions,
        "top_produits": top_produits,
    })


# -----------------------------
# Produits CRUD
# -----------------------------

def produits(request):
    if request.method == "POST":
        try:
            nom = request.POST.get("nom")
            prix = Decimal(request.POST.get("prix"))
            quantite = int(request.POST.get("quantite"))
            stock_min = int(request.POST.get("stock_min") or 0)

            fournisseur_id = request.POST.get("fournisseur")
            fournisseur = Fournisseur.objects.get(id=fournisseur_id) if fournisseur_id else None

            categorie_id = request.POST.get("categorie")
            categorie = Category.objects.get(id=categorie_id) if categorie_id else None

            image = request.FILES.get("image")

            produit = Produit.objects.create(
                nom=nom,
                prix=prix,
                quantite=quantite,
                stock_min=stock_min,
                fournisseur=fournisseur,
                category=categorie,
                image=image
            )

            qr_data = (
                f"Produit ID: {produit.id}\n"
                f"Produit: {produit.nom}\n"
                f"Prix: {produit.prix} FCFA\n"
                f"Quantité: {produit.quantite}\n"
                f"Fournisseur: {fournisseur.nom if fournisseur else 'N/A'}\n"
                f"Catégorie: {categorie.nom if categorie else 'N/A'}"
            )
            qr = qrcode.QRCode(box_size=2, border=1)
            qr.add_data(qr_data)
            qr.make(fit=True)
            img = qr.make_image()
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            produit.qr_code.save(f"produit_{produit.id}.png", File(buffer), save=False)
            produit.save()

            _create_stock_movement(
                produit=produit,
                type_mouvement="entree",
                quantite=quantite,
                stock_avant=0,
                stock_apres=produit.quantite,
                user=_get_user_or_none(request),
                note="Stock initial",
            )
            _ensure_stock_alert(produit)

            messages.success(request, f"Produit '{produit.nom}' ajouté avec succès !")
        except Exception as e:
            messages.error(request, f"Erreur lors de l'ajout du produit : {str(e)}")

        return redirect("produits")

    produits_list = Produit.objects.all()
    fournisseurs = Fournisseur.objects.all()
    categories = Category.objects.all()
    return render(request, "boutique/produits.html", {
        "produits": produits_list,
        "fournisseurs": fournisseurs,
        "categories": categories
    })


# -----------------------------
# Clients CRUD
# -----------------------------

def clients(request):
    if request.method == "POST":
        nom = request.POST.get("nom")
        telephone = request.POST.get("telephone")
        email = request.POST.get("email")

        if not nom or not telephone:
            messages.error(request, "Le nom et le téléphone sont obligatoires.")
            return redirect("clients")

        client = Client.objects.create(
            nom=nom,
            telephone=telephone,
            email=email
        )

        qr_data = (
            f"Client: {client.nom}\n"
            f"Téléphone: {client.telephone}\n"
            f"Email: {client.email if client.email else 'N/A'}"
        )
        qr = qrcode.QRCode(box_size=2, border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image()
        buffer = BytesIO()
        img.save(buffer, format="PNG")

        client.qr_code.save(f"client_{client.id}.png", File(buffer), save=False)
        client.save()

        messages.success(request, f"Client {nom} ajouté avec succès.")
        return redirect("clients")

    clients_list = Client.objects.all()
    return render(request, "boutique/clients.html", {"clients": clients_list})


# -----------------------------
# Ventes + panier
# -----------------------------

def ventes(request):
    if request.method == "POST":
        if not request.user.is_authenticated:
            messages.error(request, "Vous devez être connecté pour enregistrer une vente.")
            return redirect("ventes")

        panier = request.session.get("panier", {})
        if not panier:
            messages.error(request, "Le panier est vide.")
            return redirect("ventes")

        client_id = request.POST.get("client")
        client = Client.objects.get(id=client_id) if client_id else None
        user = request.user

        mode_paiement = (request.POST.get("paiement") or "").strip()
        paiement_info_cle = (request.POST.get("paiement_info_cle") or "").strip()
        modes_paiement_valides = {"livraison", "cash", "cheque", "mobile", "transaction_bancaire", "remise"}
        if mode_paiement and mode_paiement not in modes_paiement_valides:
            messages.error(request, "Mode de paiement invalide.")
            return redirect("ventes")
        if mode_paiement == "transaction_bancaire" and not paiement_info_cle:
            messages.error(request, "L'information clé est obligatoire pour une transaction bancaire.")
            return redirect("ventes")
        if mode_paiement != "transaction_bancaire":
            paiement_info_cle = ""
        statut_paiement = "paye" if mode_paiement else "impaye"

        cheque_image = None
        if mode_paiement == "cheque" and request.FILES.get("cheque_image"):
            cheque_image = request.FILES["cheque_image"]

        try:
            with transaction.atomic():
                vente = Vente.objects.create(
                    user=user,
                    client=client,
                    date_vente=timezone.now(),
                    total=0,
                    cheque_image=cheque_image,
                    mode_paiement=mode_paiement,
                    paiement_info_cle=paiement_info_cle,
                    statut_paiement=statut_paiement,
                    date_reglement=timezone.now() if statut_paiement == "paye" else None,
                )

                totals = _recalc_panier(panier)
                total = totals["total_after_line"]
                total_remises_lignes = totals["total_remises"]
                total_before_discount = totals["subtotal"]
                subtotal_initial = totals["subtotal_initial"]
                marge_disponible = totals["total_marge"]

                for produit_id, item in panier.items():
                    produit = Produit.objects.get(id=produit_id)
                    quantite = int(item["quantite"])
                    if quantite <= 0:
                        continue
                    if quantite > produit.quantite:
                        raise ValueError(f"Stock insuffisant pour {produit.nom}")

                    stock_avant = produit.quantite
                    prix_unitaire = Decimal(str(item.get("prix") or produit.prix))
                    remise_type = item.get("remise_type") or ""
                    remise_value = Decimal(str(item.get("remise_value") or 0))
                    total_ligne = Decimal(str(item.get("total") or 0))
                    LigneVente.objects.create(
                        vente=vente,
                        produit=produit,
                        quantite=quantite,
                        prix_unitaire=prix_unitaire,
                        remise_type=remise_type,
                        remise_value=remise_value,
                        total_ligne=total_ligne,
                    )
                    produit.quantite -= quantite
                    produit.save()
                    _create_stock_movement(
                        produit=produit,
                        type_mouvement="sortie",
                        quantite=quantite,
                        stock_avant=stock_avant,
                        stock_apres=produit.quantite,
                        user=user,
                        note="Vente",
                        vente=vente,
                    )
                    _ensure_stock_alert(produit)
                # Appliquer remise globale si fournie
                discount_type = request.POST.get("discount_type") or ""
                discount_value = Decimal(request.POST.get("discount_value") or "0")
                commission = Decimal(request.POST.get("commission") or "0")
                if discount_value < 0:
                    discount_value = Decimal("0.00")
                if commission < 0:
                    commission = Decimal("0.00")
                discount_total = Decimal("0.00")
                if discount_type == "percent":
                    discount_total = total * discount_value / Decimal("100")
                elif discount_type == "amount":
                    discount_total = discount_value
                if discount_total < 0:
                    discount_total = Decimal("0.00")
                if discount_total > marge_disponible:
                    discount_total = marge_disponible
                total = total - discount_total

                # Bon d'achat
                coupon_code = (request.POST.get("coupon_code") or "").strip().upper()
                coupon_discount = Decimal("0.00")
                if coupon_code:
                    bon = BonAchat.objects.filter(code=coupon_code, actif=True).first()
                    if bon:
                        if bon.date_expiration and bon.date_expiration < timezone.now().date():
                            messages.error(request, "Bon d'achat expiré.")
                        elif bon.usages_max and bon.usages >= bon.usages_max:
                            messages.error(request, "Bon d'achat déjà utilisé.")
                        else:
                            if bon.type_remise == "percent":
                                coupon_discount = total * bon.valeur / Decimal("100")
                            else:
                                coupon_discount = bon.valeur
                            marge_restante = total - subtotal_initial
                            if marge_restante < 0:
                                marge_restante = Decimal("0.00")
                            if coupon_discount > marge_restante:
                                coupon_discount = marge_restante
                            total = total - coupon_discount
                            bon.usages += 1
                            bon.save(update_fields=["usages"])
                    else:
                        messages.error(request, "Bon d'achat invalide.")
                if total < 0:
                    total = Decimal("0.00")

                vente.total_before_discount = total_before_discount
                vente.total_discount = discount_total + total_remises_lignes + coupon_discount
                vente.discount_type = discount_type
                vente.discount_value = discount_value
                vente.coupon_code = coupon_code
                vente.coupon_discount = coupon_discount
                vente.commission = commission
                vente.total = total
                vente.save()
        except Exception as e:
            messages.error(request, f"Erreur lors de la vente : {e}")
            return redirect("ventes")

        request.session["panier"] = {}
        request.session.modified = True

        if vente.statut_paiement == "impaye":
            messages.success(request, f"Facture #{vente.id} enregistrée en impayé.")
        else:
            messages.success(request, f"Vente #{vente.id} enregistrée avec succès.")
        return redirect("ventes")

    produits_list = Produit.objects.all()
    clients_list = Client.objects.all()
    panier = request.session.get("panier", {})
    if not panier:
        panier = {}

    totals = _recalc_panier(panier)

    return render(request, "boutique/ventes.html", {
        "produits": produits_list,
        "clients": clients_list,
        "panier": panier,
        "total_general": totals["total_after_line"],
        "subtotal": totals["subtotal"],
        "remises_lignes": totals["total_remises"],
        "marge_totale": totals["total_marge"],
    })


def ajouter_panier(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))

        produit = Produit.objects.get(id=produit_id)
        panier = request.session.get("panier", {})

        if produit_id in panier:
            panier[produit_id]["quantite"] += 1
        else:
            panier[produit_id] = {
                "nom": produit.nom,
                "prix": float(produit.prix),
                "prix_initial": float(produit.prix),
                "quantite": 1,
                "remise_type": "",
                "remise_value": 0,
            }

        totals = _recalc_panier(panier)

        request.session["panier"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def retirer_panier(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))

        panier = request.session.get("panier", {})

        if produit_id in panier:
            panier[produit_id]["quantite"] -= 1
            if panier[produit_id]["quantite"] <= 0:
                del panier[produit_id]

        totals = _recalc_panier(panier)

        request.session["panier"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def vider_panier(request):
    if request.method == "POST":
        request.session["panier"] = {}
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": {},
            "total_general": 0,
            "subtotal": 0,
            "remises_lignes": 0,
            "marge_totale": 0,
        })
        return JsonResponse({"html": html})


def modifier_quantite(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))
        quantite = int(data.get("quantite", 1))

        panier = request.session.get("panier", {})

        if produit_id in panier:
            if quantite > 0:
                panier[produit_id]["quantite"] = quantite
            else:
                del panier[produit_id]

        totals = _recalc_panier(panier)

        request.session["panier"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def supprimer_du_panier(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))

        panier = request.session.get("panier", {})

        if produit_id in panier:
            del panier[produit_id]

        totals = _recalc_panier(panier)

        request.session["panier"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def update_remise_panier(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))
        remise_type = data.get("remise_type") or ""
        remise_value = data.get("remise_value") or 0

        panier = request.session.get("panier", {})
        if produit_id in panier:
            panier[produit_id]["remise_type"] = remise_type
            try:
                panier[produit_id]["remise_value"] = float(remise_value)
            except ValueError:
                panier[produit_id]["remise_value"] = 0

        totals = _recalc_panier(panier)

        request.session["panier"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def update_prix_panier(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))
        prix = data.get("prix")

        panier = request.session.get("panier", {})
        if produit_id in panier:
            try:
                prix_decimal = Decimal(str(prix))
            except (InvalidOperation, ValueError, TypeError):
                return JsonResponse({"error": "Prix invalide."}, status=400)
            prix_initial = Decimal(str(panier[produit_id].get("prix_initial") or panier[produit_id]["prix"]))
            if prix_decimal < prix_initial:
                prix_decimal = prix_initial
            panier[produit_id]["prix"] = float(prix_decimal)

        totals = _recalc_panier(panier)

        request.session["panier"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def ajouter_panier_proforma(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))

        produit = Produit.objects.get(id=produit_id)
        panier = request.session.get("panier_proforma", {})

        if produit_id in panier:
            panier[produit_id]["quantite"] += 1
        else:
            panier[produit_id] = {
                "nom": produit.nom,
                "prix": float(produit.prix),
                "prix_initial": float(produit.prix),
                "quantite": 1,
                "remise_type": "",
                "remise_value": 0,
            }

        totals = _recalc_panier(panier)

        request.session["panier_proforma"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def retirer_panier_proforma(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))

        panier = request.session.get("panier_proforma", {})

        if produit_id in panier:
            panier[produit_id]["quantite"] -= 1
            if panier[produit_id]["quantite"] <= 0:
                del panier[produit_id]

        totals = _recalc_panier(panier)

        request.session["panier_proforma"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def vider_panier_proforma(request):
    if request.method == "POST":
        request.session["panier_proforma"] = {}
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": {},
            "total_general": 0,
            "subtotal": 0,
            "remises_lignes": 0,
            "marge_totale": 0,
        })
        return JsonResponse({"html": html})


def modifier_quantite_proforma(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))
        quantite = int(data.get("quantite", 1))

        panier = request.session.get("panier_proforma", {})

        if produit_id in panier:
            if quantite > 0:
                panier[produit_id]["quantite"] = quantite
            else:
                del panier[produit_id]

        totals = _recalc_panier(panier)

        request.session["panier_proforma"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def supprimer_du_panier_proforma(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))

        panier = request.session.get("panier_proforma", {})

        if produit_id in panier:
            del panier[produit_id]

        totals = _recalc_panier(panier)

        request.session["panier_proforma"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def update_remise_panier_proforma(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))
        remise_type = data.get("remise_type") or ""
        remise_value = data.get("remise_value") or 0

        panier = request.session.get("panier_proforma", {})
        if produit_id in panier:
            panier[produit_id]["remise_type"] = remise_type
            try:
                panier[produit_id]["remise_value"] = float(remise_value)
            except ValueError:
                panier[produit_id]["remise_value"] = 0

        totals = _recalc_panier(panier)

        request.session["panier_proforma"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


def update_prix_panier_proforma(request):
    if request.method == "POST":
        data = json.loads(request.body)
        produit_id = str(data.get("produit_id"))
        prix = data.get("prix")

        panier = request.session.get("panier_proforma", {})
        if produit_id in panier:
            try:
                prix_decimal = Decimal(str(prix))
            except (InvalidOperation, ValueError, TypeError):
                return JsonResponse({"error": "Prix invalide."}, status=400)
            prix_initial = Decimal(str(panier[produit_id].get("prix_initial") or panier[produit_id]["prix"]))
            if prix_decimal < prix_initial:
                prix_decimal = prix_initial
            panier[produit_id]["prix"] = float(prix_decimal)

        totals = _recalc_panier(panier)

        request.session["panier_proforma"] = panier
        request.session.modified = True

        html = render_to_string("boutique/panier_content.html", {
            "panier": panier,
            "total_general": totals["total_after_line"],
            "subtotal": totals["subtotal"],
            "remises_lignes": totals["total_remises"],
            "marge_totale": totals["total_marge"],
        })
        return JsonResponse({"html": html})


# -----------------------------
# Facture PDF A4
# -----------------------------

def facture_pdf(request, vente_id):
    vente = get_object_or_404(Vente, id=vente_id)
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename="facture_{vente.id}.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>Boutique INOVA</b>", styles["Title"]))
    elements.append(Paragraph(f"Facture N° {vente.id}", styles["Normal"]))
    elements.append(Paragraph(f"Date : {vente.date_vente.strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    statut_label = "IMPAYE" if vente.statut_paiement == "impaye" else "PAYE"
    statut_color = "red" if vente.statut_paiement == "impaye" else "green"
    elements.append(Paragraph(f'Statut paiement : <font color="{statut_color}"><b>{statut_label}</b></font>', styles["Normal"]))
    if vente.mode_paiement:
        elements.append(Paragraph(f"Mode paiement : {vente.get_mode_paiement_display()}", styles["Normal"]))
    if vente.mode_paiement == "transaction_bancaire" and vente.paiement_info_cle:
        elements.append(Paragraph(f"Info clé : {vente.paiement_info_cle}", styles["Normal"]))
    elements.append(Spacer(1,12))

    data = [["Produit","Quantité","Prix Unitaire","Sous-total"]]
    for ligne in vente.lignes.all():
        data.append([ligne.produit.nom, str(ligne.quantite), f"{ligne.prix_unitaire} FCFA", f"{ligne.quantite * ligne.prix_unitaire} FCFA"])
    data.append(["","","<b>TOTAL</b>", f"<b>{vente.total} FCFA</b>"])

    table = Table(data, colWidths=[80*mm,30*mm,40*mm,40*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.grey),
        ("TEXTCOLOR",(0,0),(-1,0),colors.whitesmoke),
        ("ALIGN",(1,1),(-1,-1),"CENTER"),
        ("GRID",(0,0),(-1,-1),0.5,colors.black),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("BACKGROUND",(-2,-1),(-1,-1),colors.lightgrey)
    ]))
    elements.append(table)
    elements.append(Spacer(1,20))
    elements.append(Paragraph("Merci pour votre achat !", styles["Italic"]))
    doc.build(elements)
    return response


# -----------------------------
# Ticket de caisse 80mm
# -----------------------------

def ticket_caisse_pdf(request, vente_id):
    vente = get_object_or_404(Vente, id=vente_id)
    largeur_pt = 80 * 2.83465
    hauteur_pt = 300
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename="ticket_{vente.id}.pdf"'
    c = canvas.Canvas(response, pagesize=(largeur_pt, hauteur_pt))
    y = hauteur_pt - 10

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(largeur_pt/2, y, "BOUTIQUE INOVA")
    y -= 15
    c.setFont("Helvetica", 8)
    c.drawCentredString(largeur_pt/2, y, f"Facture N° {vente.id}")
    y -= 12
    c.drawCentredString(largeur_pt/2, y, f"Date : {vente.date_vente.strftime('%d/%m/%Y %H:%M')}")
    y -= 15
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(largeur_pt/2, y, f"Statut: {'IMPAYE' if vente.statut_paiement == 'impaye' else 'PAYE'}")
    y -= 10
    if vente.mode_paiement:
        c.setFont("Helvetica", 7)
        c.drawCentredString(largeur_pt/2, y, f"Paiement: {vente.get_mode_paiement_display()}")
        y -= 10
    c.line(5, y, largeur_pt-5, y)
    y -= 10
    c.setFont("Helvetica-Bold", 8)
    c.drawString(5, y, "Produit")
    c.drawRightString(largeur_pt-5, y, "Total")
    y -= 10
    c.line(5, y, largeur_pt-5, y)
    y -= 5
    c.setFont("Helvetica", 8)
    for ligne in vente.lignes.all():
        nom = ligne.produit.nom[:15]
        sous_total = ligne.quantite * ligne.prix_unitaire
        c.drawString(5, y, f"{nom} x{ligne.quantite}")
        c.drawRightString(largeur_pt-5, y, f"{sous_total} FCFA")
        y -= 10
    y -= 5
    c.line(5, y, largeur_pt-5, y)
    y -= 10
    c.setFont("Helvetica-Bold", 9)
    c.drawString(5, y, "TOTAL")
    c.drawRightString(largeur_pt-5, y, f"{vente.total} FCFA")
    y -= 20
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(largeur_pt/2, y, "Merci pour votre achat !")
    y -= 10
    c.drawCentredString(largeur_pt/2, y, "A bientôt !")
    c.showPage()
    c.save()
    return response


# -----------------------------
# Inventaire (scan QR)
# -----------------------------

def inventaire(request):
    produits_list = Produit.objects.all().order_by("nom")
    users_list = User.objects.all().order_by("username")

    if request.method == "POST":
        current_produit_filter = request.POST.get("current_produit_filter", "").strip()
        current_user_filter = request.POST.get("current_user_filter", "").strip()
        current_date_from = request.POST.get("current_date_from", "").strip()
        current_date_to = request.POST.get("current_date_to", "").strip()

        def _redirect_inventaire(produit_value=None):
            params = {}
            if produit_value:
                params["produit_id"] = produit_value
            elif current_produit_filter:
                params["produit_id"] = current_produit_filter
            if current_user_filter:
                params["user_id"] = current_user_filter
            if current_date_from:
                params["date_from"] = current_date_from
            if current_date_to:
                params["date_to"] = current_date_to
            url = reverse("inventaire")
            if params:
                url = f"{url}?{urlencode(params)}"
            return redirect(url)

        produit_id = request.POST.get("produit_id")
        scan_data = (request.POST.get("scan_data") or "").strip()
        mode = request.POST.get("mode")
        quantite = int(request.POST.get("quantite") or 0)

        if scan_data and not produit_id:
            # Try parse "Produit ID: X" or "Produit: NAME"
            if "Produit ID:" in scan_data:
                try:
                    produit_id = scan_data.split("Produit ID:")[1].split()[0].strip()
                except Exception:
                    produit_id = None
            elif "Produit:" in scan_data:
                try:
                    name = scan_data.split("Produit:")[1].splitlines()[0].strip()
                    produit = Produit.objects.filter(nom__iexact=name).first()
                    produit_id = produit.id if produit else None
                except Exception:
                    produit_id = None

        if not produit_id:
            messages.error(request, "Produit introuvable via scan ou sélection.")
            return _redirect_inventaire()

        produit = Produit.objects.get(id=produit_id)
        stock_avant = produit.quantite

        if mode == "ajouter":
            produit.quantite += quantite
        elif mode == "retirer":
            if quantite > produit.quantite:
                messages.error(request, "Stock insuffisant pour retrait.")
                return _redirect_inventaire(produit_id)
            produit.quantite -= quantite
        elif mode == "fixer":
            produit.quantite = quantite
        else:
            messages.error(request, "Mode d'inventaire invalide.")
            return _redirect_inventaire(produit_id)

        produit.save()
        _create_stock_movement(
            produit=produit,
            type_mouvement="ajustement",
            quantite=produit.quantite - stock_avant,
            stock_avant=stock_avant,
            stock_apres=produit.quantite,
            user=_get_user_or_none(request),
            note="Inventaire",
        )
        _ensure_stock_alert(produit)
        messages.success(request, f"Stock mis à jour pour {produit.nom}.")
        return _redirect_inventaire(produit_id)

    mouvements = StockMovement.objects.select_related("produit", "user").order_by("-created_at")
    produit_filter = request.GET.get("produit_id")
    user_filter = request.GET.get("user_id")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if produit_filter:
        mouvements = mouvements.filter(produit_id=produit_filter)
    if user_filter:
        mouvements = mouvements.filter(user_id=user_filter)
    if date_from:
        mouvements = mouvements.filter(created_at__date__gte=date_from)
    if date_to:
        mouvements = mouvements.filter(created_at__date__lte=date_to)

    return render(request, "boutique/inventaire.html", {
        "produits": produits_list,
        "users": users_list,
        "mouvements": mouvements[:300],
        "filters": {
            "produit_id": produit_filter or "",
            "user_id": user_filter or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }
    })


def inventaire_export_csv(request):
    mouvements = StockMovement.objects.select_related("produit", "user").order_by("-created_at")
    produit_filter = request.GET.get("produit_id")
    user_filter = request.GET.get("user_id")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if produit_filter:
        mouvements = mouvements.filter(produit_id=produit_filter)
    if user_filter:
        mouvements = mouvements.filter(user_id=user_filter)
    if date_from:
        mouvements = mouvements.filter(created_at__date__gte=date_from)
    if date_to:
        mouvements = mouvements.filter(created_at__date__lte=date_to)

    rows = []
    for m in mouvements:
        rows.append([
            m.created_at.strftime("%d/%m/%Y %H:%M"),
            m.produit.nom,
            m.type_mouvement,
            m.quantite,
            m.stock_avant,
            m.stock_apres,
            m.user.username if m.user else "N/A",
            m.note,
        ])
    header = ["Date", "Produit", "Type", "Qté", "Avant", "Après", "Utilisateur", "Note"]
    return _csv_response("inventaire.csv", header, rows)


def inventaire_export_pdf(request):
    mouvements = StockMovement.objects.select_related("produit", "user").order_by("-created_at")
    produit_filter = request.GET.get("produit_id")
    user_filter = request.GET.get("user_id")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    if produit_filter:
        mouvements = mouvements.filter(produit_id=produit_filter)
    if user_filter:
        mouvements = mouvements.filter(user_id=user_filter)
    if date_from:
        mouvements = mouvements.filter(created_at__date__gte=date_from)
    if date_to:
        mouvements = mouvements.filter(created_at__date__lte=date_to)

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="inventaire.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("<b>Historique Inventaire</b>", styles["Title"]))
    elements.append(Spacer(1, 12))

    data = [["Date", "Produit", "Type", "Qté", "Avant", "Après", "Utilisateur"]]
    for m in mouvements[:300]:
        data.append([
            m.created_at.strftime("%d/%m/%Y %H:%M"),
            m.produit.nom,
            m.type_mouvement,
            str(m.quantite),
            str(m.stock_avant),
            str(m.stock_apres),
            m.user.username if m.user else "N/A",
        ])
    table = Table(data, colWidths=[30*mm, 40*mm, 20*mm, 12*mm, 12*mm, 12*mm, 30*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (1, 1), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    doc.build(elements)
    return response


# -----------------------------
# Alertes dashboard
# -----------------------------

def alertes_lues(request, alerte_id):
    alerte = get_object_or_404(Alerte, id=alerte_id)
    alerte.lue = True
    alerte.save()
    return redirect("dashboard")


# -----------------------------
# Stock
# -----------------------------

def stock(request):
    if request.method == "POST":
        try:
            produit_id = request.POST.get("produit_id")
            type_mouvement = request.POST.get("type_mouvement")
            quantite = int(request.POST.get("quantite") or 0)
            note = request.POST.get("note", "").strip()
            reference = request.POST.get("reference", "").strip()
            cout_reparation = Decimal(request.POST.get("cout_reparation") or "0")
            date_prevue_retour = parse_date(request.POST.get("date_prevue_retour") or "")
            date_effective = parse_date(request.POST.get("date_effective") or "") or timezone.localdate()

            if not produit_id or not type_mouvement or quantite == 0:
                messages.error(request, "Veuillez renseigner le produit, le type et la quantité.")
                return redirect("stock")

            produit = Produit.objects.get(id=produit_id)
            stock_avant = produit.quantite

            suivi_action = ""
            suivi_etat = ""
            if type_mouvement == "entree":
                if quantite < 0:
                    messages.error(request, "La quantité d'entrée doit être positive.")
                    return redirect("stock")
                produit.quantite += quantite
                operation_label = "Entrée stock"
            elif type_mouvement == "sortie":
                if quantite < 0:
                    messages.error(request, "La quantité de sortie doit être positive.")
                    return redirect("stock")
                if quantite > produit.quantite:
                    messages.error(request, "Stock insuffisant pour cette sortie.")
                    return redirect("stock")
                produit.quantite -= quantite
                operation_label = "Sortie stock"
            elif type_mouvement == "ajustement":
                produit.quantite += quantite
                operation_label = "Ajustement stock"
            elif type_mouvement == "classer_defectueux":
                if quantite < 0:
                    messages.error(request, "La quantité doit être positive.")
                    return redirect("stock")
                if quantite > produit.quantite:
                    messages.error(request, "Stock disponible insuffisant pour classer en défectueux.")
                    return redirect("stock")
                produit.quantite -= quantite
                produit.quantite_defectueuse += quantite
                operation_label = "Classement défectueux"
                suivi_action = "classification"
                suivi_etat = "defectueux"
            elif type_mouvement == "envoyer_reparation":
                if quantite < 0:
                    messages.error(request, "La quantité doit être positive.")
                    return redirect("stock")
                if quantite > produit.quantite_defectueuse:
                    messages.error(request, "Stock défectueux insuffisant pour envoyer en réparation.")
                    return redirect("stock")
                produit.quantite_defectueuse -= quantite
                produit.quantite_reparation += quantite
                operation_label = "Envoi en réparation"
                suivi_action = "envoi_reparation"
                suivi_etat = "reparation"
            elif type_mouvement == "retour_reparation":
                if quantite < 0:
                    messages.error(request, "La quantité doit être positive.")
                    return redirect("stock")
                if quantite > produit.quantite_reparation:
                    messages.error(request, "Stock en réparation insuffisant pour retour.")
                    return redirect("stock")
                produit.quantite_reparation -= quantite
                produit.quantite += quantite
                operation_label = "Retour de réparation"
                suivi_action = "retour_reparation"
                suivi_etat = "disponible"
            elif type_mouvement == "rebut_defectueux":
                if quantite < 0:
                    messages.error(request, "La quantité doit être positive.")
                    return redirect("stock")
                if quantite > produit.quantite_defectueuse:
                    messages.error(request, "Stock défectueux insuffisant pour rebut.")
                    return redirect("stock")
                produit.quantite_defectueuse -= quantite
                operation_label = "Rebut défectueux"
                suivi_action = "rebut"
                suivi_etat = "rebut"
            else:
                messages.error(request, "Type de mouvement invalide.")
                return redirect("stock")

            produit.save(update_fields=["quantite", "quantite_defectueuse", "quantite_reparation"])

            stock_apres = produit.quantite
            movement_type = type_mouvement if type_mouvement in {"entree", "sortie", "ajustement"} else "ajustement"
            note_parts = [operation_label]
            if note:
                note_parts.append(note)
            note_complet = " | ".join(note_parts)
            _create_stock_movement(
                produit=produit,
                type_mouvement=movement_type,
                quantite=quantite,
                stock_avant=stock_avant,
                stock_apres=stock_apres,
                user=_get_user_or_none(request),
                note=note_complet,
            )

            if suivi_action:
                SuiviProduitEtat.objects.create(
                    produit=produit,
                    action=suivi_action,
                    etat_apres=suivi_etat,
                    quantite=abs(quantite),
                    reference=reference,
                    cout_reparation=cout_reparation if cout_reparation > 0 else Decimal("0.00"),
                    date_prevue_retour=date_prevue_retour if suivi_action == "envoi_reparation" else None,
                    date_effective=date_effective,
                    note=note,
                    user=_get_user_or_none(request),
                )
            _ensure_stock_alert(produit)

            messages.success(request, "Mouvement de stock enregistré.")
            return redirect("stock")
        except Exception as e:
            messages.error(request, f"Erreur stock : {e}")
            return redirect("stock")

    produits_list = Produit.objects.all().order_by("nom")
    mouvements = StockMovement.objects.select_related("produit", "user").order_by("-created_at")[:200]
    suivis = SuiviProduitEtat.objects.select_related("produit", "user").order_by("-created_at")[:200]
    return render(request, "boutique/stock.html", {
        "produits": produits_list,
        "mouvements": mouvements,
        "suivis": suivis,
    })


def stock_export_csv(request):
    rows = []
    for p in Produit.objects.all().order_by("nom"):
        rows.append([
            p.id,
            p.nom,
            p.quantite,
            p.quantite_defectueuse,
            p.quantite_reparation,
            p.stock_min,
            p.fournisseur.nom if p.fournisseur else "N/A",
            p.category.nom if p.category else "N/A",
        ])
    header = ["ID", "Produit", "Stock disponible", "Stock défectueux", "Stock en réparation", "Stock min", "Fournisseur", "Catégorie"]
    return _csv_response("stock.csv", header, rows)


def stock_export_pdf(request):
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="stock.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("<b>Rapport Stock</b>", styles["Title"]))
    elements.append(Spacer(1, 12))

    data = [["Produit", "Disponible", "Défectueux", "Réparation", "Stock min", "Fournisseur", "Catégorie"]]
    for p in Produit.objects.all().order_by("nom"):
        data.append([
            p.nom,
            str(p.quantite),
            str(p.quantite_defectueuse),
            str(p.quantite_reparation),
            str(p.stock_min),
            p.fournisseur.nom if p.fournisseur else "N/A",
            p.category.nom if p.category else "N/A",
        ])

    table = Table(data, colWidths=[45*mm, 18*mm, 20*mm, 20*mm, 18*mm, 34*mm, 30*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (1, 1), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    elements.append(table)
    doc.build(elements)
    return response


# -----------------------------
# Personnel
# -----------------------------

def personnel(request):
    if request.method == "POST":
        try:
            nom = request.POST.get("nom")
            prenom = request.POST.get("prenom", "")
            telephone = request.POST.get("telephone")
            email = request.POST.get("email") or None
            poste = request.POST.get("poste")
            date_embauche = request.POST.get("date_embauche")
            salaire_base = Decimal(request.POST.get("salaire_base") or "0")
            statut = request.POST.get("statut", "actif")

            if not nom or not telephone or not poste:
                messages.error(request, "Nom, téléphone et poste sont obligatoires.")
                return redirect("personnel")

            Personnel.objects.create(
                nom=nom,
                prenom=prenom,
                telephone=telephone,
                email=email,
                poste=poste,
                date_embauche=date_embauche or timezone.now().date(),
                salaire_base=salaire_base,
                statut=statut,
            )
            messages.success(request, "Personnel ajouté avec succès.")
            return redirect("personnel")
        except Exception as e:
            messages.error(request, f"Erreur personnel : {e}")
            return redirect("personnel")

    personnels = Personnel.objects.all().order_by("-created_at")
    return render(request, "boutique/personnel.html", {"personnels": personnels})


def personnel_export_csv(request):
    rows = []
    for p in Personnel.objects.all().order_by("nom"):
        rows.append([
            p.nom,
            p.prenom,
            p.poste,
            p.telephone,
            p.email or "",
            f"{p.salaire_base}",
            p.statut,
        ])
    header = ["Nom", "Prénom", "Poste", "Téléphone", "Email", "Salaire base", "Statut"]
    return _csv_response("personnel.csv", header, rows)


def personnel_export_pdf(request):
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="personnel.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("<b>Rapport Personnel</b>", styles["Title"]))
    elements.append(Spacer(1, 12))

    data = [["Nom", "Poste", "Téléphone", "Email", "Salaire"]]
    for p in Personnel.objects.all().order_by("nom"):
        data.append([
            f"{p.prenom} {p.nom}".strip(),
            p.poste,
            p.telephone,
            p.email or "N/A",
            f"{p.salaire_base}",
        ])

    table = Table(data, colWidths=[45*mm, 35*mm, 30*mm, 45*mm, 25*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (1, 1), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    elements.append(table)
    doc.build(elements)
    return response


# -----------------------------
# Salaires
# -----------------------------

def salaires(request):
    def _parse_non_negative_decimal(field_name):
        raw = (request.POST.get(field_name) or "0").strip()
        try:
            value = Decimal(raw or "0")
        except (InvalidOperation, ValueError):
            raise ValueError(f"Valeur invalide pour {field_name}.")
        if value < 0:
            raise ValueError(f"{field_name} doit être >= 0.")
        return value

    def _create_paie_for_personnel(emp, periode_date, heures, taux_horaire, prime, retenue):
        if Paie.objects.filter(personnel=emp, periode=periode_date).exists():
            raise ValueError(f"Paie déjà enregistrée pour {emp} à la période {periode_date}.")

        avances_qs = AvanceSalaire.objects.filter(
            personnel=emp,
            statut="ouverte",
            date_avance__lte=periode_date,
        )
        avances_total = avances_qs.aggregate(total=Sum("montant")).get("total") or Decimal("0.00")

        with transaction.atomic():
            paie = Paie.objects.create(
                personnel=emp,
                periode=periode_date,
                heures=heures,
                taux_horaire=taux_horaire,
                prime=prime,
                avance=avances_total,
                retenue=retenue,
                salaire_base=emp.salaire_base,
            )
            if avances_total > 0:
                avances_qs.update(statut="soldee", paie=paie, date_solde=timezone.now())
        return paie

    if request.method == "POST":
        try:
            action = (request.POST.get("action") or "payer_un").strip()
            if action == "avance":
                personnel_id = request.POST.get("personnel_id")
                date_avance_raw = request.POST.get("date_avance")
                note = (request.POST.get("note") or "").strip()
                montant = _parse_non_negative_decimal("montant")
                if montant <= 0:
                    messages.error(request, "Le montant de l'avance doit être supérieur à 0.")
                    return redirect("salaires")
                if not personnel_id:
                    messages.error(request, "Sélectionnez un salarié pour l'avance.")
                    return redirect("salaires")
                date_avance = parse_date(date_avance_raw) if date_avance_raw else timezone.localdate()
                if not date_avance:
                    messages.error(request, "Date d'avance invalide.")
                    return redirect("salaires")
                emp = Personnel.objects.get(id=personnel_id)
                AvanceSalaire.objects.create(
                    personnel=emp,
                    montant=montant,
                    date_avance=date_avance,
                    note=note,
                )
                messages.success(request, f"Avance enregistrée pour {emp}.")
                return redirect("salaires")

            periode_raw = request.POST.get("periode")
            periode_date = parse_date(periode_raw) if periode_raw else None
            if not periode_date:
                messages.error(request, "Veuillez renseigner une période valide.")
                return redirect("salaires")

            heures = _parse_non_negative_decimal("heures")
            taux_horaire = _parse_non_negative_decimal("taux_horaire")
            prime = _parse_non_negative_decimal("prime")
            retenue = _parse_non_negative_decimal("retenue")

            if action == "payer_un":
                personnel_id = request.POST.get("personnel_id")
                if not personnel_id:
                    messages.error(request, "Veuillez sélectionner un salarié.")
                    return redirect("salaires")
                emp = Personnel.objects.get(id=personnel_id)
                _create_paie_for_personnel(emp, periode_date, heures, taux_horaire, prime, retenue)
                messages.success(request, f"Paie enregistrée pour {emp}.")
                return redirect("salaires")

            if action == "payer_lot":
                exclude_ids = []
                for item in request.POST.getlist("exclude_ids"):
                    if str(item).isdigit():
                        exclude_ids.append(int(item))

                personnels_qs = Personnel.objects.filter(statut="actif").order_by("nom", "prenom")
                if exclude_ids:
                    personnels_qs = personnels_qs.exclude(id__in=exclude_ids)

                personnels_to_pay = list(personnels_qs)
                if not personnels_to_pay:
                    messages.error(request, "Aucun salarié à payer après exclusions.")
                    return redirect("salaires")

                created = 0
                skipped = []
                for emp in personnels_to_pay:
                    try:
                        _create_paie_for_personnel(emp, periode_date, heures, taux_horaire, prime, retenue)
                        created += 1
                    except ValueError as e:
                        skipped.append(str(e))
                if created:
                    messages.success(request, f"{created} paie(s) enregistrée(s) en lot.")
                if skipped:
                    messages.error(request, "Certaines paies ignorées: " + " | ".join(skipped[:5]))
                return redirect("salaires")

            messages.error(request, "Action de salaire invalide.")
            return redirect("salaires")
        except Exception as e:
            messages.error(request, f"Erreur paie : {e}")
            return redirect("salaires")

    personnels = Personnel.objects.all().order_by("nom")
    personnels_actifs = personnels.filter(statut="actif")
    paies = Paie.objects.select_related("personnel").order_by("-created_at")[:200]
    avances = AvanceSalaire.objects.select_related("personnel", "paie").order_by("-created_at")[:200]
    avances_ouvertes_total = (
        AvanceSalaire.objects.filter(statut="ouverte").aggregate(total=Sum("montant")).get("total")
        or Decimal("0.00")
    )
    return render(request, "boutique/salaires.html", {
        "personnels": personnels,
        "personnels_actifs": personnels_actifs,
        "paies": paies,
        "avances": avances,
        "avances_ouvertes_total": avances_ouvertes_total,
    })


def salaires_export_csv(request):
    rows = []
    for p in Paie.objects.select_related("personnel").order_by("-created_at"):
        rows.append([
            p.periode.strftime("%d/%m/%Y"),
            f"{p.personnel.prenom} {p.personnel.nom}".strip(),
            f"{p.heures}",
            f"{p.taux_horaire}",
            f"{p.prime}",
            f"{p.avance}",
            f"{p.retenue}",
            f"{p.total}",
        ])
    header = ["Période", "Personnel", "Heures", "Taux", "Prime", "Avance", "Retenue", "Total"]
    return _csv_response("salaires.csv", header, rows)


def salaires_export_pdf(request):
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="salaires.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("<b>Rapport Salaires</b>", styles["Title"]))
    elements.append(Spacer(1, 12))

    data = [["Période", "Personnel", "Heures", "Taux", "Prime", "Avance", "Retenue", "Total"]]
    for p in Paie.objects.select_related("personnel").order_by("-created_at")[:200]:
        data.append([
            p.periode.strftime("%d/%m/%Y"),
            f"{p.personnel.prenom} {p.personnel.nom}".strip(),
            f"{p.heures}",
            f"{p.taux_horaire}",
            f"{p.prime}",
            f"{p.avance}",
            f"{p.retenue}",
            f"{p.total}",
        ])

    table = Table(data, colWidths=[22*mm, 35*mm, 15*mm, 15*mm, 15*mm, 15*mm, 15*mm, 18*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (1, 1), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    doc.build(elements)
    return response


# -----------------------------
# Paiement (simulation)
# -----------------------------

def payer(request):
    if request.method == "POST":
        mode = request.POST.get("moneyType")
        numero = request.POST.get(f"{mode}_numero")
        montant = 5000

        if mode == "orange":
            message = f"Simulation : paiement {montant} FCFA via Orange Money pour le numéro {numero}"
            return JsonResponse({"status": "success", "message": message})
        return JsonResponse({"status": "error", "message": "Mode de paiement inconnu"})

    return render(request, "checkout.html")


def bons_achat(request):
    if request.method == "POST":
        try:
            code = (request.POST.get("code") or "").strip().upper()
            type_remise = request.POST.get("type_remise")
            valeur = Decimal(request.POST.get("valeur") or "0")
            date_expiration = request.POST.get("date_expiration") or None
            usages_max = int(request.POST.get("usages_max") or 0)
            actif = request.POST.get("actif") == "1"
            if not code or not type_remise:
                messages.error(request, "Code et type requis.")
                return redirect("bons_achat")
            BonAchat.objects.create(
                code=code,
                type_remise=type_remise,
                valeur=valeur,
                date_expiration=date_expiration,
                usages_max=usages_max,
                actif=actif,
            )
            messages.success(request, "Bon d'achat créé.")
            return redirect("bons_achat")
        except Exception as e:
            messages.error(request, f"Erreur: {e}")
            return redirect("bons_achat")

    bons = BonAchat.objects.all().order_by("-created_at")
    return render(request, "boutique/bons_achat.html", {"bons": bons})


def remises_report(request):
    ventes = Vente.objects.order_by("-date_vente")
    total_remises = ventes.aggregate(total=Sum("total_discount")).get("total") or Decimal("0.00")
    total_commissions = ventes.aggregate(total=Sum("commission")).get("total") or Decimal("0.00")
    return render(request, "boutique/remises_report.html", {
        "ventes": ventes[:200],
        "total_remises": total_remises,
        "total_commissions": total_commissions,
    })


def comptabilite(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "depense").strip()
        type_depense = (request.POST.get("type_depense") or "").strip()
        nature = (request.POST.get("nature") or "").strip()
        montant_raw = (request.POST.get("montant") or "").strip()
        note = (request.POST.get("note") or "").strip()
        date_depense_raw = (request.POST.get("date_depense") or "").strip()
        period_post = (request.POST.get("period") or "month").strip()
        date_from_post = (request.POST.get("date_from") or "").strip()
        date_to_post = (request.POST.get("date_to") or "").strip()

        params = {"period": period_post}
        if date_from_post:
            params["date_from"] = date_from_post
        if date_to_post:
            params["date_to"] = date_to_post
        redirect_url = reverse("comptabilite")
        if params:
            redirect_url = f"{redirect_url}?{urlencode(params)}"

        if action == "ranson_journalier":
            date_jour_raw = (request.POST.get("date_jour") or "").strip()
            montant_ranson_raw = (request.POST.get("montant_ranson") or "").strip()
            note_ranson = (request.POST.get("note_ranson") or "").strip()
            date_jour = parse_date(date_jour_raw) if date_jour_raw else timezone.localdate()
            if not date_jour:
                messages.error(request, "Date de contrôle invalide.")
                return redirect(redirect_url)
            try:
                montant_ranson = Decimal(montant_ranson_raw or "0")
            except (InvalidOperation, ValueError):
                messages.error(request, "Montant de rançon invalide.")
                return redirect(redirect_url)
            if montant_ranson < 0:
                messages.error(request, "Le montant de rançon doit être >= 0.")
                return redirect(redirect_url)

            present_ids = set()
            for item in request.POST.getlist("present_ids"):
                if str(item).isdigit():
                    present_ids.add(int(item))

            actifs = list(Personnel.objects.filter(statut="actif").order_by("nom", "prenom"))
            if not actifs:
                messages.error(request, "Aucun employé actif pour le contrôle journalier.")
                return redirect(redirect_url)

            now_dt = timezone.now()
            present_count = 0
            absent_count = 0
            total_paye = Decimal("0.00")
            for emp in actifs:
                is_present = emp.id in present_ids
                if is_present:
                    present_count += 1
                    total_paye += montant_ranson
                else:
                    absent_count += 1

                RansonJournalier.objects.update_or_create(
                    personnel=emp,
                    date_jour=date_jour,
                    defaults={
                        "present": is_present,
                        "montant": montant_ranson if is_present else Decimal("0.00"),
                        "est_paye": is_present,
                        "date_paiement": now_dt if is_present else None,
                        "note": note_ranson,
                        "controle_par": _get_user_or_none(request),
                    },
                )

            messages.success(
                request,
                f"Contrôle journalier enregistré. Présents: {present_count}, absents: {absent_count}, total payé: {total_paye} FCFA.",
            )
            return redirect(redirect_url)

        if type_depense not in {"investissement", "morte"}:
            messages.error(request, "Type de dépense invalide.")
            return redirect(redirect_url)
        if not nature:
            messages.error(request, "La nature de la dépense est obligatoire.")
            return redirect(redirect_url)
        try:
            montant = Decimal(montant_raw or "0")
        except (InvalidOperation, ValueError):
            messages.error(request, "Montant de dépense invalide.")
            return redirect(redirect_url)
        if montant <= 0:
            messages.error(request, "Le montant doit être supérieur à 0.")
            return redirect(redirect_url)
        date_depense = parse_date(date_depense_raw) if date_depense_raw else timezone.localdate()
        if not date_depense:
            messages.error(request, "Date de dépense invalide.")
            return redirect(redirect_url)

        Depense.objects.create(
            type_depense=type_depense,
            nature=nature,
            montant=montant,
            date_depense=date_depense,
            note=note,
            user=_get_user_or_none(request),
        )
        messages.success(request, "Nouvelle dépense enregistrée.")
        return redirect(redirect_url)

    today = timezone.localdate()
    date_from_raw = (request.GET.get("date_from") or "").strip()
    date_to_raw = (request.GET.get("date_to") or "").strip()
    period = (request.GET.get("period") or "month").strip()
    parsed_from = parse_date(date_from_raw) if date_from_raw else None
    parsed_to = parse_date(date_to_raw) if date_to_raw else None

    if parsed_from and parsed_to:
        date_from = parsed_from
        date_to = parsed_to
    else:
        if period == "year":
            date_from = today.replace(month=1, day=1)
            date_to = today
        elif period == "week":
            date_from = today - timedelta(days=today.weekday())
            date_to = today
        else:
            date_from = today.replace(day=1)
            date_to = today

    ventes_qs = Vente.objects.select_related("client").filter(date_vente__date__gte=date_from, date_vente__date__lte=date_to)
    paies_qs = Paie.objects.select_related("personnel").filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    achats_qs = BonCommande.objects.select_related("fournisseur", "produit").filter(
        date_commande__date__gte=date_from,
        date_commande__date__lte=date_to,
    ).exclude(statut="annule")
    depenses_qs = Depense.objects.select_related("user").filter(date_depense__gte=date_from, date_depense__lte=date_to)
    ransons_qs = RansonJournalier.objects.select_related("personnel", "controle_par").filter(
        date_jour__gte=date_from,
        date_jour__lte=date_to,
    )

    ca_total = ventes_qs.aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    encaisse_total = ventes_qs.filter(statut_paiement="paye").aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    impaye_total = ventes_qs.filter(statut_paiement="impaye").aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    remises_total = ventes_qs.aggregate(total=Sum("total_discount")).get("total") or Decimal("0.00")
    commissions_total = ventes_qs.aggregate(total=Sum("commission")).get("total") or Decimal("0.00")
    salaires_total = paies_qs.aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    achats_total = achats_qs.aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    depenses_total = depenses_qs.aggregate(total=Sum("montant")).get("total") or Decimal("0.00")
    depenses_investissement = depenses_qs.filter(type_depense="investissement").aggregate(total=Sum("montant")).get("total") or Decimal("0.00")
    depenses_mortes = depenses_qs.filter(type_depense="morte").aggregate(total=Sum("montant")).get("total") or Decimal("0.00")
    ransons_total = ransons_qs.filter(est_paye=True).aggregate(total=Sum("montant")).get("total") or Decimal("0.00")
    ransons_presents = ransons_qs.filter(present=True).count()
    ransons_absents = ransons_qs.filter(present=False).count()
    charges_total = salaires_total + achats_total + commissions_total + depenses_total + ransons_total
    resultat_net = encaisse_total - charges_total
    taux_encaissement = (encaisse_total / ca_total * Decimal("100")) if ca_total > 0 else Decimal("0.00")

    stats_paiement = list(
        ventes_qs.values("mode_paiement")
        .annotate(total=Sum("total"), count=Count("id"))
        .order_by("-total")
    )
    mode_labels = {
        "livraison": "Paiement a la livraison",
        "cash": "Cash",
        "cheque": "Cheque",
        "mobile": "Orange Money",
        "transaction_bancaire": "Transaction bancaire",
        "remise": "Remise vendeur",
        "": "Non defini / impaye",
    }
    for item in stats_paiement:
        key = item.get("mode_paiement") or ""
        item["label"] = mode_labels.get(key, key or "Non defini")
        item["ratio"] = float((item["total"] / ca_total * Decimal("100")) if ca_total > 0 else Decimal("0.00"))

    flux_journalier = list(
        ventes_qs.annotate(jour=TruncDate("date_vente"))
        .values("jour")
        .annotate(
            facture=Sum("total"),
            encaisse=Sum("total", filter=Q(statut_paiement="paye")),
            impaye=Sum("total", filter=Q(statut_paiement="impaye")),
        )
        .order_by("-jour")[:14]
    )

    recent_ventes = ventes_qs.order_by("-date_vente")[:12]
    recent_paies = paies_qs.order_by("-created_at")[:10]
    recent_achats = achats_qs.order_by("-date_commande")[:10]
    recent_depenses = depenses_qs.order_by("-date_depense", "-created_at")[:12]
    recent_ransons = ransons_qs.order_by("-date_jour", "-updated_at")[:20]
    personnels_actifs = Personnel.objects.filter(statut="actif").order_by("nom", "prenom")

    context = {
        "date_from": str(date_from),
        "date_to": str(date_to),
        "period": period,
        "ca_total": ca_total,
        "encaisse_total": encaisse_total,
        "impaye_total": impaye_total,
        "remises_total": remises_total,
        "commissions_total": commissions_total,
        "salaires_total": salaires_total,
        "achats_total": achats_total,
        "depenses_total": depenses_total,
        "depenses_investissement": depenses_investissement,
        "depenses_mortes": depenses_mortes,
        "ransons_total": ransons_total,
        "ransons_presents": ransons_presents,
        "ransons_absents": ransons_absents,
        "charges_total": charges_total,
        "resultat_net": resultat_net,
        "taux_encaissement": taux_encaissement,
        "factures_count": ventes_qs.count(),
        "impayes_count": ventes_qs.filter(statut_paiement="impaye").count(),
        "stats_paiement": stats_paiement,
        "flux_journalier": flux_journalier,
        "recent_ventes": recent_ventes,
        "recent_paies": recent_paies,
        "recent_achats": recent_achats,
        "recent_depenses": recent_depenses,
        "recent_ransons": recent_ransons,
        "personnels_actifs": personnels_actifs,
    }
    return render(request, "boutique/comptabilite.html", context)


def factures(request):
    ventes_qs = Vente.objects.select_related("client").order_by("-date_vente")

    q = (request.GET.get("q") or "").strip()
    client_id = (request.GET.get("client") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    min_total_raw = (request.GET.get("min_total") or "").strip()
    max_total_raw = (request.GET.get("max_total") or "").strip()

    if q:
        q_filter = Q(client__nom__icontains=q)
        if q.isdigit():
            q_filter |= Q(id=int(q))
        ventes_qs = ventes_qs.filter(q_filter)

    if client_id.isdigit():
        ventes_qs = ventes_qs.filter(client_id=int(client_id))

    if date_from:
        ventes_qs = ventes_qs.filter(date_vente__date__gte=date_from)
    if date_to:
        ventes_qs = ventes_qs.filter(date_vente__date__lte=date_to)

    try:
        if min_total_raw:
            min_total = Decimal(min_total_raw)
            if min_total >= 0:
                ventes_qs = ventes_qs.filter(total__gte=min_total)
    except (InvalidOperation, ValueError):
        pass

    try:
        if max_total_raw:
            max_total = Decimal(max_total_raw)
            if max_total >= 0:
                ventes_qs = ventes_qs.filter(total__lte=max_total)
    except (InvalidOperation, ValueError):
        pass

    total_factures = ventes_qs.aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    total_commissions = ventes_qs.aggregate(total=Sum("commission")).get("total") or Decimal("0.00")
    resultats_count = ventes_qs.count()
    ventes = ventes_qs[:300]
    clients = Client.objects.order_by("nom")

    return render(request, "boutique/factures.html", {
        "ventes": ventes,
        "clients": clients,
        "total_factures": total_factures,
        "total_commissions": total_commissions,
        "resultats_count": resultats_count,
        "q": q,
        "client_id": client_id,
        "date_from": date_from,
        "date_to": date_to,
        "min_total": min_total_raw,
        "max_total": max_total_raw,
    })


def facture_marquer_payee(request, vente_id):
    if request.method != "POST":
        return redirect("factures")

    vente = get_object_or_404(Vente, id=vente_id)
    if vente.statut_paiement == "paye":
        messages.info(request, f"La facture #{vente.id} est déjà payée.")
        return redirect("factures")

    mode_paiement = (request.POST.get("paiement") or "").strip()
    paiement_info_cle = (request.POST.get("paiement_info_cle") or "").strip()
    modes_paiement_valides = {"livraison", "cash", "cheque", "mobile", "transaction_bancaire", "remise"}

    if mode_paiement not in modes_paiement_valides:
        messages.error(request, "Sélectionnez un mode de paiement pour encaisser cette facture.")
        return redirect("factures")
    if mode_paiement == "transaction_bancaire" and not paiement_info_cle:
        messages.error(request, "L'information clé est obligatoire pour la transaction bancaire.")
        return redirect("factures")

    if mode_paiement != "transaction_bancaire":
        paiement_info_cle = ""

    vente.mode_paiement = mode_paiement
    vente.paiement_info_cle = paiement_info_cle
    vente.statut_paiement = "paye"
    vente.date_reglement = timezone.now()
    vente.save(update_fields=["mode_paiement", "paiement_info_cle", "statut_paiement", "date_reglement"])

    messages.success(request, f"Facture #{vente.id} encaissée avec succès.")
    return redirect("factures")


def commandes_magasin(request):
    if request.method == "POST":
        try:
            produit_id = request.POST.get("produit_id")
            quantite = int(request.POST.get("quantite") or 0)
            message = (request.POST.get("message") or "").strip()
            if not produit_id or quantite <= 0:
                messages.error(request, "Produit et quantité sont obligatoires.")
                return redirect("commandes_magasin")

            produit = Produit.objects.get(id=produit_id)
            stock_magasin = StockMagasin.objects.filter(produit=produit).first()
            stock_disponible = stock_magasin.quantite if stock_magasin else 0
            if quantite > stock_disponible:
                alternatives_qs = (
                    StockMagasin.objects.select_related("produit")
                    .filter(quantite__gte=quantite)
                    .exclude(produit_id=produit.id)
                    .order_by("-quantite", "produit__nom")[:5]
                )
                if alternatives_qs:
                    alternatives = ", ".join([f"{s.produit.nom} ({s.quantite})" for s in alternatives_qs])
                    messages.error(
                        request,
                        f"Stock magasin insuffisant pour {produit.nom} (disponible {stock_disponible}, demandé {quantite}). "
                        f"Produits alternatifs: {alternatives}.",
                    )
                else:
                    messages.error(
                        request,
                        f"Stock magasin insuffisant pour {produit.nom} (disponible {stock_disponible}, demandé {quantite}).",
                    )
                return redirect("commandes_magasin")

            commande = CommandeInterne.objects.create(
                produit=produit,
                quantite=quantite,
                message=message,
                boutique_user=_get_user_or_none(request),
            )
            CommandeInterneAudit.objects.create(
                commande=commande,
                action="emission",
                espace="boutique",
                acteur=_get_user_or_none(request),
                details=f"Commande émise vers magasin: produit={produit.nom}, quantité={quantite}.",
            )
            messages.success(request, f"Commande interne {commande.numero} envoyée au magasin.")
            return redirect("commandes_magasin")
        except Exception as e:
            messages.error(request, f"Erreur commande magasin : {e}")
            return redirect("commandes_magasin")

    commandes = CommandeInterne.objects.select_related(
        "produit", "boutique_user", "magasinier_user", "validation_user"
    ).all()
    audits = CommandeInterneAudit.objects.select_related("commande", "acteur")[:250]
    produits_list = Produit.objects.order_by("nom")
    stocks_magasin = StockMagasin.objects.select_related("produit").order_by("produit__nom")
    return render(
        request,
        "boutique/commandes_magasin.html",
        {
            "commandes": commandes[:300],
            "produits": produits_list,
            "stocks_magasin": stocks_magasin[:200],
            "audits": audits,
        },
    )


def valider_reception_commande_magasin(request, commande_id):
    if request.method != "POST":
        return redirect("commandes_magasin")

    commande = get_object_or_404(CommandeInterne, id=commande_id)
    if commande.statut != "expediee":
        messages.error(request, "Cette commande n'est pas en attente de validation boutique.")
        return redirect("commandes_magasin")

    preuve = request.FILES.get("preuve_reception_image")
    if not preuve:
        messages.error(request, "L'image du bordereau reçu est obligatoire pour valider.")
        return redirect("commandes_magasin")
    qr_scan = (request.POST.get("qr_scan") or "").strip()
    if not qr_scan:
        messages.error(request, "Le scan QR du bordereau est obligatoire pour valider.")
        return redirect("commandes_magasin")

    expected_token = (commande.qr_token or "").upper()
    scanned_token = ""
    scan_upper = qr_scan.upper()
    if "TOKEN:" in scan_upper:
        scanned_token = scan_upper.split("TOKEN:", 1)[1].split("|")[0].strip()
    else:
        scanned_token = scan_upper.strip()
    if scanned_token != expected_token:
        messages.error(request, "QR bordereau invalide. Validation refusée.")
        return redirect("commandes_magasin")

    with transaction.atomic():
        produit = Produit.objects.select_for_update().get(id=commande.produit_id)
        stock_avant = produit.quantite
        produit.quantite += commande.quantite
        produit.save(update_fields=["quantite"])
        _create_stock_movement(
            produit=produit,
            type_mouvement="entree",
            quantite=commande.quantite,
            stock_avant=stock_avant,
            stock_apres=produit.quantite,
            user=_get_user_or_none(request),
            note=f"Réception commande magasin {commande.numero}",
        )
        _ensure_stock_alert(produit)

        commande.statut = "validee"
        commande.preuve_reception_image = preuve
        commande.date_validation = timezone.now()
        commande.validation_user = _get_user_or_none(request)
        commande.stock_avant_reception = stock_avant
        commande.stock_apres_reception = produit.quantite
        commande.save(
            update_fields=[
                "statut",
                "preuve_reception_image",
                "date_validation",
                "validation_user",
                "stock_avant_reception",
                "stock_apres_reception",
                "updated_at",
            ]
        )
        CommandeInterneAudit.objects.create(
            commande=commande,
            action="validation",
            espace="boutique",
            acteur=_get_user_or_none(request),
            details=(
                f"Réception validée avec preuve. "
                f"Stock: {stock_avant} -> {produit.quantite}. Bordereau: {commande.bordereau_numero or '-'}."
            ),
        )

    messages.success(request, f"Commande {commande.numero} validée et stock mis à jour.")
    return redirect("commandes_magasin")


def bon_commandes(request):
    if request.method == "POST":
        try:
            fournisseur_id = request.POST.get("fournisseur")
            produit_id = request.POST.get("produit")
            quantite = int(request.POST.get("quantite") or 1)
            prix_unitaire = Decimal(request.POST.get("prix_unitaire") or "0")
            statut = request.POST.get("statut") or "brouillon"
            note = request.POST.get("note") or ""

            if not produit_id:
                messages.error(request, "Le produit est obligatoire.")
                return redirect("bon_commandes")
            if quantite <= 0:
                messages.error(request, "La quantité doit être supérieure à 0.")
                return redirect("bon_commandes")
            if prix_unitaire < 0:
                prix_unitaire = Decimal("0.00")

            BonCommande.objects.create(
                fournisseur_id=int(fournisseur_id) if fournisseur_id else None,
                produit_id=int(produit_id),
                quantite=quantite,
                prix_unitaire=prix_unitaire,
                statut=statut,
                note=note,
            )
            messages.success(request, "Bon de commande enregistré.")
            return redirect("bon_commandes")
        except Exception as e:
            messages.error(request, f"Erreur bon de commande : {e}")
            return redirect("bon_commandes")

    bons = BonCommande.objects.select_related("fournisseur", "produit").order_by("-date_commande")[:200]
    fournisseurs = Fournisseur.objects.order_by("nom")
    produits = Produit.objects.order_by("nom")
    total_bons = bons.aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    return render(request, "boutique/bon_commandes.html", {
        "bons": bons,
        "fournisseurs": fournisseurs,
        "produits": produits,
        "total_bons": total_bons,
    })


def bon_commande_pdf(request, bon_id):
    bon = get_object_or_404(BonCommande, id=bon_id)
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="bon_commande_{bon.id}.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>Bon de commande</b>", styles["Title"]))
    elements.append(Paragraph(f"BC N° {bon.id}", styles["Normal"]))
    elements.append(Paragraph(f"Date : {bon.date_commande.strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    data = [["Fournisseur", "Produit", "Quantité", "Prix unitaire", "Total"]]
    data.append([
        bon.fournisseur.nom if bon.fournisseur else "N/A",
        bon.produit.nom if bon.produit else "N/A",
        str(bon.quantite),
        f"{bon.prix_unitaire} FCFA",
        f"{bon.total} FCFA",
    ])
    table = Table(data, colWidths=[45*mm, 55*mm, 25*mm, 30*mm, 30*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Statut: {bon.get_statut_display()}", styles["Normal"]))
    if bon.note:
        elements.append(Paragraph(f"Note: {bon.note}", styles["Normal"]))
    doc.build(elements)
    return response


def proformas(request):
    if request.method == "POST":
        try:
            panier = request.session.get("panier_proforma", {})
            if not panier:
                messages.error(request, "Le panier proforma est vide.")
                return redirect("proformas")

            client_id = request.POST.get("client")
            statut = request.POST.get("statut") or "brouillon"
            date_expiration = request.POST.get("date_expiration") or None
            discount_type = request.POST.get("discount_type") or ""
            discount_value = Decimal(request.POST.get("discount_value") or "0")
            commission = Decimal(request.POST.get("commission") or "0")
            coupon_code = (request.POST.get("coupon_code") or "").strip().upper()
            note = request.POST.get("note") or ""

            totals = _recalc_panier(panier)
            total = totals["total_after_line"]
            total_remises_lignes = totals["total_remises"]
            total_before_discount = totals["subtotal"]
            subtotal_initial = totals["subtotal_initial"]
            marge_disponible = totals["total_marge"]

            if discount_value < 0:
                discount_value = Decimal("0.00")
            if commission < 0:
                commission = Decimal("0.00")

            discount_total = Decimal("0.00")
            if discount_type == "percent":
                discount_total = total * discount_value / Decimal("100")
            elif discount_type == "amount":
                discount_total = discount_value
            if discount_total < 0:
                discount_total = Decimal("0.00")
            if discount_total > marge_disponible:
                discount_total = marge_disponible
            total = total - discount_total

            coupon_discount = Decimal("0.00")
            if coupon_code:
                bon = BonAchat.objects.filter(code=coupon_code, actif=True).first()
                if bon:
                    if bon.date_expiration and bon.date_expiration < timezone.now().date():
                        messages.error(request, "Bon d'achat expiré.")
                    elif bon.usages_max and bon.usages >= bon.usages_max:
                        messages.error(request, "Bon d'achat déjà utilisé.")
                    else:
                        if bon.type_remise == "percent":
                            coupon_discount = total * bon.valeur / Decimal("100")
                        else:
                            coupon_discount = bon.valeur
                        marge_restante = total - subtotal_initial
                        if marge_restante < 0:
                            marge_restante = Decimal("0.00")
                        if coupon_discount > marge_restante:
                            coupon_discount = marge_restante
                        total = total - coupon_discount
                        bon.usages += 1
                        bon.save(update_fields=["usages"])
                else:
                    messages.error(request, "Bon d'achat invalide.")
            if total < 0:
                total = Decimal("0.00")

            with transaction.atomic():
                proforma = FactureProforma.objects.create(
                    client_id=int(client_id) if client_id else None,
                    statut=statut,
                    date_expiration=date_expiration,
                    note=note,
                    total=total,
                    total_before_discount=total_before_discount,
                    total_discount=discount_total + total_remises_lignes + coupon_discount,
                    discount_type=discount_type,
                    discount_value=discount_value,
                    coupon_code=coupon_code,
                    coupon_discount=coupon_discount,
                    commission=commission,
                )

                first_line = None
                total_quantite = 0
                for produit_id, item in panier.items():
                    produit = Produit.objects.get(id=produit_id)
                    quantite = int(item["quantite"])
                    if quantite <= 0:
                        continue
                    prix_unitaire = Decimal(str(item.get("prix") or produit.prix))
                    remise_type = item.get("remise_type") or ""
                    remise_value = Decimal(str(item.get("remise_value") or 0))
                    total_ligne = Decimal(str(item.get("total") or 0))
                    ligne = LigneProforma.objects.create(
                        proforma=proforma,
                        produit=produit,
                        quantite=quantite,
                        prix_unitaire=prix_unitaire,
                        remise_type=remise_type,
                        remise_value=remise_value,
                        total_ligne=total_ligne,
                    )
                    total_quantite += quantite
                    if first_line is None:
                        first_line = ligne

                # Compatibilité avec les anciens champs proforma mono-produit.
                if first_line:
                    proforma.produit = first_line.produit
                    proforma.quantite = total_quantite
                    proforma.prix_unitaire = first_line.prix_unitaire
                    proforma.remise = proforma.total_discount
                    proforma.save(update_fields=["produit", "quantite", "prix_unitaire", "remise"])

            request.session["panier_proforma"] = {}
            request.session.modified = True
            messages.success(request, "Facture proforma enregistrée.")
            return redirect("proformas")
        except Exception as e:
            messages.error(request, f"Erreur proforma : {e}")
            return redirect("proformas")

    proformas_list = FactureProforma.objects.select_related("client", "produit", "vente_convertie").order_by("-date_creation")[:200]
    clients = Client.objects.order_by("nom")
    produits = Produit.objects.order_by("nom")
    panier = request.session.get("panier_proforma", {})
    totals = _recalc_panier(panier)
    total_proformas = proformas_list.aggregate(total=Sum("total")).get("total") or Decimal("0.00")
    return render(request, "boutique/proformas.html", {
        "proformas": proformas_list,
        "clients": clients,
        "produits": produits,
        "panier": panier,
        "total_general": totals["total_after_line"],
        "subtotal": totals["subtotal"],
        "remises_lignes": totals["total_remises"],
        "marge_totale": totals["total_marge"],
        "total_proformas": total_proformas,
    })


def proforma_pdf(request, proforma_id):
    proforma = get_object_or_404(FactureProforma, id=proforma_id)
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="proforma_{proforma.id}.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>Facture proforma</b>", styles["Title"]))
    elements.append(Paragraph(f"Proforma N° {proforma.id}", styles["Normal"]))
    elements.append(Paragraph(f"Date : {proforma.date_creation.strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    if proforma.date_expiration:
        elements.append(Paragraph(f"Expire le : {proforma.date_expiration.strftime('%d/%m/%Y')}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    data = [["Produit", "Quantité", "Prix unitaire", "Remise", "Sous-total"]]
    lignes = proforma.lignes.all()
    if lignes.exists():
        for ligne in lignes:
            produit_nom = ligne.produit.nom if ligne.produit else "N/A"
            data.append([
                produit_nom,
                str(ligne.quantite),
                f"{ligne.prix_unitaire} FCFA",
                f"{ligne.remise_value} ({ligne.remise_type or '-'})",
                f"{ligne.total_ligne} FCFA",
            ])
    else:
        data.append([
            proforma.produit.nom if proforma.produit else "N/A",
            str(proforma.quantite),
            f"{proforma.prix_unitaire} FCFA",
            f"{proforma.remise} FCFA",
            f"{proforma.total} FCFA",
        ])

    data.append(["", "", "", "Total avant", f"{proforma.total_before_discount} FCFA"])
    data.append(["", "", "", "Total remises", f"{proforma.total_discount} FCFA"])
    data.append(["", "", "", "Total final", f"{proforma.total} FCFA"])
    table = Table(data, colWidths=[55*mm, 25*mm, 35*mm, 35*mm, 35*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))
    if proforma.client:
        elements.append(Paragraph(f"Client: {proforma.client.nom}", styles["Normal"]))
    elements.append(Paragraph(f"Statut: {proforma.get_statut_display()}", styles["Normal"]))
    if proforma.note:
        elements.append(Paragraph(f"Note: {proforma.note}", styles["Normal"]))
    doc.build(elements)
    return response


def proforma_ticket_a4_pdf(request, proforma_id):
    proforma = get_object_or_404(FactureProforma, id=proforma_id)
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="ticket_proforma_a4_{proforma.id}.pdf"'
    doc = SimpleDocTemplate(response, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>Ticket Proforma (A4)</b>", styles["Title"]))
    elements.append(Paragraph(f"N° Proforma: {proforma.id}", styles["Normal"]))
    elements.append(Paragraph(f"Date: {proforma.date_creation.strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    if proforma.client:
        elements.append(Paragraph(f"Client: {proforma.client.nom}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    data = [["Produit", "Qte", "Prix U.", "Remise", "Total ligne"]]
    lignes = proforma.lignes.all()
    if lignes.exists():
        for ligne in lignes:
            data.append([
                ligne.produit.nom if ligne.produit else "N/A",
                str(ligne.quantite),
                f"{ligne.prix_unitaire} FCFA",
                f"{ligne.remise_value}",
                f"{ligne.total_ligne} FCFA",
            ])
    else:
        data.append([
            proforma.produit.nom if proforma.produit else "N/A",
            str(proforma.quantite),
            f"{proforma.prix_unitaire} FCFA",
            f"{proforma.remise}",
            f"{proforma.total} FCFA",
        ])

    data.append(["", "", "", "Sous-total", f"{proforma.total_before_discount} FCFA"])
    data.append(["", "", "", "Remises", f"{proforma.total_discount} FCFA"])
    data.append(["", "", "", "<b>Total</b>", f"<b>{proforma.total} FCFA</b>"])

    table = Table(data, colWidths=[70*mm, 20*mm, 30*mm, 30*mm, 35*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Document proforma (A4).", styles["Italic"]))
    doc.build(elements)
    return response


def proforma_convertir(request, proforma_id):
    if request.method != "POST":
        return redirect("proformas")

    proforma = get_object_or_404(FactureProforma, id=proforma_id)
    if proforma.vente_convertie_id:
        messages.warning(request, f"Cette proforma est déjà convertie en facture #{proforma.vente_convertie_id}.")
        return redirect("proformas")

    if proforma.statut == "expiree":
        messages.error(request, "Conversion impossible: cette proforma est expirée.")
        return redirect("proformas")

    try:
        with transaction.atomic():
            lignes = list(proforma.lignes.select_related("produit"))
            if not lignes and proforma.produit:
                lignes = [LigneProforma(
                    proforma=proforma,
                    produit=proforma.produit,
                    quantite=proforma.quantite,
                    prix_unitaire=proforma.prix_unitaire,
                    remise_type="amount" if proforma.remise > 0 else "",
                    remise_value=proforma.remise,
                    total_ligne=proforma.total,
                )]
            if not lignes:
                raise ValueError("Aucune ligne de produit sur la proforma.")

            produits_map = {}
            for ligne in lignes:
                if not ligne.produit_id:
                    raise ValueError("Un produit de la proforma n'existe plus.")
                produits_map[ligne.produit_id] = Produit.objects.select_for_update().get(id=ligne.produit_id)

            for ligne in lignes:
                quantite = int(ligne.quantite)
                if quantite <= 0:
                    raise ValueError("Quantité invalide sur une ligne.")
                produit = produits_map[ligne.produit_id]
                if quantite > produit.quantite:
                    raise ValueError(f"Stock insuffisant pour {produit.nom}.")

            user = request.user if request.user.is_authenticated else None
            vente = Vente.objects.create(
                user=user,
                client=proforma.client,
                date_vente=timezone.now(),
                total=proforma.total,
                total_before_discount=proforma.total_before_discount,
                total_discount=proforma.total_discount,
                discount_type=proforma.discount_type,
                discount_value=proforma.discount_value,
                coupon_code=proforma.coupon_code,
                coupon_discount=proforma.coupon_discount,
                commission=proforma.commission,
            )

            for ligne in lignes:
                quantite = int(ligne.quantite)
                produit = produits_map[ligne.produit_id]
                LigneVente.objects.create(
                    vente=vente,
                    produit=produit,
                    quantite=quantite,
                    prix_unitaire=ligne.prix_unitaire,
                    remise_type=ligne.remise_type,
                    remise_value=ligne.remise_value,
                    total_ligne=ligne.total_ligne,
                )

                stock_avant = produit.quantite
                produit.quantite -= quantite
                produit.save(update_fields=["quantite"])
                _create_stock_movement(
                    produit=produit,
                    type_mouvement="sortie",
                    quantite=quantite,
                    stock_avant=stock_avant,
                    stock_apres=produit.quantite,
                    user=user,
                    note=f"Conversion proforma #{proforma.id}",
                    vente=vente,
                )
                _ensure_stock_alert(produit)

            proforma.statut = "acceptee"
            proforma.vente_convertie = vente
            proforma.date_conversion = timezone.now()
            proforma.save(update_fields=["statut", "vente_convertie", "date_conversion"])

        messages.success(request, f"Proforma #{proforma.id} convertie en facture #{vente.id}.")
    except Exception as e:
        messages.error(request, f"Erreur conversion proforma : {e}")

    return redirect("proformas")


@csrf_exempt
def update_produit(request, pk):
    produit = Produit.objects.get(pk=pk)

    if request.method == "POST" and request.FILES.get("image"):
        try:
            produit.image = request.FILES["image"]

            qr_content = f"Produit ID: {produit.id}\nProduit: {produit.nom}\nPrix: {produit.prix} FCFA\nStock:{produit.quantite}"
            if produit.fournisseur:
                qr_content += f" - Fournisseur: {produit.fournisseur.nom}"
            if produit.category:
                qr_content += f" - Catégorie: {produit.category.nom}"

            qr_img = qrcode.make(qr_content)
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            produit.qr_code.save(f"qr_{produit.id}.png", ContentFile(buffer.getvalue()), save=False)

            produit.save()

            return JsonResponse({
                "success": True,
                "image_url": produit.image.url,
                "qr_code_url": produit.qr_code.url,
            })
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            field = data.get("field")
            value = data.get("value")

            if field == "prix":
                cleaned_value = value.replace("\xa0", "").replace(" ", "").replace(",", ".")
                try:
                    produit.prix = Decimal(cleaned_value)
                except InvalidOperation:
                    return JsonResponse({"success": False, "error": f"Valeur invalide pour le prix: {value}"})

            elif field == "quantite":
                try:
                    stock_avant = produit.quantite
                    produit.quantite = int(value)
                except ValueError:
                    return JsonResponse({"success": False, "error": f"Valeur invalide pour la quantité: {value}"})

                if produit.quantite != stock_avant:
                    _create_stock_movement(
                        produit=produit,
                        type_mouvement="ajustement",
                        quantite=produit.quantite - stock_avant,
                        stock_avant=stock_avant,
                        stock_apres=produit.quantite,
                        user=_get_user_or_none(request),
                        note="Modification manuelle",
                    )
                    _ensure_stock_alert(produit)

            elif field == "nom":
                produit.nom = value

            elif field == "fournisseur":
                produit.fournisseur_id = int(value) if value else None

            elif field == "categorie":
                produit.category_id = int(value) if value else None

            elif field == "stock_min":
                try:
                    produit.stock_min = int(value)
                except ValueError:
                    return JsonResponse({"success": False, "error": f"Valeur invalide pour le stock min: {value}"})
                _ensure_stock_alert(produit)

            else:
                return JsonResponse({"success": False, "error": "Champ non reconnu"})

            qr_content = f"Produit ID: {produit.id}\nProduit: {produit.nom}\nPrix: {produit.prix} FCFA\nStock:{produit.quantite}"
            if produit.fournisseur:
                qr_content += f" - Fournisseur: {produit.fournisseur.nom}"
            if produit.category:
                qr_content += f" - Catégorie: {produit.category.nom}"

            qr_img = qrcode.make(qr_content)
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            produit.qr_code.save(f"qr_{produit.id}.png", ContentFile(buffer.getvalue()), save=False)

            produit.save()

            return JsonResponse({
                "success": True,
                "qr_code_url": produit.qr_code.url
            })

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    return JsonResponse({"success": False, "error": "Méthode non autorisée"})


@csrf_exempt
def update_client(request, pk):
    client = Client.objects.get(pk=pk)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            field = data.get("field")
            value = data.get("value")

            if field == "nom":
                client.nom = value
            elif field == "telephone":
                client.telephone = value
            elif field == "email":
                client.email = value
            else:
                return JsonResponse({"success": False, "error": "Champ non reconnu"})

            qr_content = f"Client: {client.nom} - Tel: {client.telephone}"
            if client.email:
                qr_content += f" - Email: {client.email}"

            qr_img = qrcode.make(qr_content)
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            client.qr_code.save(f"qr_client_{client.id}.png", ContentFile(buffer.getvalue()), save=False)

            client.save()

            return JsonResponse({
                "success": True,
                "qr_code_url": client.qr_code.url
            })

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    return JsonResponse({"success": False, "error": "Méthode non autorisée"})


@csrf_exempt
def ajouter_fournisseur(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            nom = data.get("nom")
            contact = data.get("contact")
            email = data.get("email")

            if not nom:
                return JsonResponse({"success": False, "error": "Le nom est requis."})
            if not contact:
                return JsonResponse({"success": False, "error": "Le contact est requis."})
            if not email:
                return JsonResponse({"success": False, "error": "L'email est requis."})

            fournisseur = Fournisseur.objects.create(
                nom=nom,
                contact=contact,
                email=email
            )
            return JsonResponse({"success": True, "id": fournisseur.id, "nom": fournisseur.nom})

        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

    return JsonResponse({"success": False, "error": "Méthode non autorisée."})


@csrf_exempt
def ajouter_categorie(request):
    if request.method == "POST":
        data = json.loads(request.body)
        nom = data.get("nom")
        Category.objects.create(nom=nom)
        return JsonResponse({"success": True})
    return JsonResponse({"success": False, "error":"Méthode non autorisée"})
