// -----------------------------
// CSRF Token Helper (Django)
// -----------------------------
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

// -----------------------------
// Fonction générique POST JSON
// -----------------------------
async function postData(url, data) {
    let response = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken")
        },
        body: JSON.stringify(data)
    });

    if (!response.ok) {
        console.error("Erreur réseau :", response.statusText);
        return;
    }

    let result = await response.json();
    document.getElementById("panierContent").innerHTML = result.html;
}

// -----------------------------
// Gestion du panier
// -----------------------------
function ajouterAuPanier(produitId) {
    postData("/ajouter_panier/", { produit_id: produitId });
}

function retirerDuPanier(produitId) {
    postData("/retirer_panier/", { produit_id: produitId });
}

function supprimerDuPanier(produitId) {
    postData("/supprimer_du_panier/", { produit_id: produitId });
}

function modifierQuantite(produitId, quantite) {
    postData("/modifier_quantite/", { produit_id: produitId, quantite: quantite });
}

function viderPanier() {
    postData("/vider_panier/", {});
}

// -----------------------------
// Paiement Cash & Calcul Rendu
// -----------------------------
