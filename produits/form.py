from django.forms import ModelForm
from .models import *
from django import forms



class AjoutProduit(ModelForm):
    class Meta:
        model= Produit
        fields=[
            'nom', 'category','prix', 'quantite', 'date_expiration','image'
        ]

        widgets={
            'name':forms.TextInput(
                attrs={
                    'plceholder':'Entrez le nom du produit',
                    'class':'form-control',
                }

            ),
            'category':forms.Select(
                attrs={
                    'class':'form-control',
                }

            ),
            'prix':forms.NumberInput(
                attrs={
                    'plceholder':'Entrez le prix du produit',
                    'class':'form-control',
                }

            ),
            'quantite':forms.NumberInput(
                attrs={
                    'plceholder':'Entrez la quantité du produit',
                    'class':'form-control',
             }

            ),
            'date_expiration':forms.DateInput(
                attrs={
                    'plceholder':'Date d\'expiration',
                    'class':'form-control',
                    'type':'date'
                }

            ),
            'image':forms.FileInput(
                attrs={
                    'plceholder':'Entrez le nom du produit',
                    'class':'form-control-file',
                }
            )

        }


        def __init__(self, *args, **kwargs ):

            super(AjoutProduit, self).__init__(*args, **kwargs)

            self.fields['nom'].error_messages={
                'required':'Le nom du produit est obligatoire',
                'invalid':'Veuillez renseigner le nom'
            }

            self.fields['category'].error_messages={
                'required':'La categorie du produit est obligatoire',
                'invalid':'veuillez selectionner la categuorie'
            }

            self.fields['prix'].error_messages={
                'required':'Le prix du produit est obligatoire',
                'invalid':'Veuillez renseigner le prix'
            }
            self.fields['quatite'].error_messages={
                'required':'La quantité du produit est obligatoire',
                'invalid':'Veillez entrer la quantité'
            }
        
            self.fields['date_expiration'].error_messages={
                'required':'La date d\'expiration du produit est obligatoire',
                'invalid':'Veillez entrer une date valide'
            }
            