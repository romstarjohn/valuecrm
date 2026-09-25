# Recette interne — pilote Tara Money / ClickFunnels

Version 0.1 — plan de vérification proposé, non exécuté.

Référence : [cadrage du produit](PRODUCT_CADRAGE_PMI.md).

Session cible : dimanche 20 septembre 2026. Réalisateurs et autorité d’acceptation : gestionnaires Valued. L’accord d’ouverture est distinct de la réalisation d’une démonstration.

## 1. Préparation

Identifier l’environnement, les cours de test, les comptes client, gestionnaire et administrateur, les connexions Tara / ClickFunnels et l’expéditeur des e-mails. Éviter d’utiliser des dossiers clients existants comme données de test.

Confirmer si les essais fournisseur sont simulés ou réels. Un résultat simulé vérifie notre logique ; il ne prouve pas à lui seul que l’opération fonctionne sur le compte fournisseur du vendeur. Consigner séparément les deux niveaux de preuve.

Jeu de données proposé : un produit associé à un cours de test, un paiement unique de 1 000 XAF et un plan de trois versements de 400 XAF. Ces montants servent uniquement à la recette et ne fixent aucun tarif commercial. Prévoir un deuxième cours pour vérifier qu’une action ne touche pas les autres accès du client.

Pour chaque scénario, renseigner : exécutant, date, environnement, références de test, résultat attendu et obtenu, preuve, anomalie éventuelle, état final. Statuts proposés : non exécuté, réussi, échoué, bloqué.

## 2. Scénarios et traçabilité

| ID | Exigences | Scénario | Résultat attendu |
|---|---|---|---|
| T01 | P01, P02 | Importer un cours puis créer son produit dans notre plateforme | Le cours conserve sa référence ClickFunnels ; le produit commercial est local et associé au bon cours |
| T02 | P03, P04, P15 | Ouvrir un lien produit depuis un site externe sur mobile et ordinateur | Le bon produit, sa marque, son mockup, ses bénéfices et ses plans sont affichés |
| T03 | P03, P07 | Omettre successivement prénom, nom, e-mail ou téléphone | La soumission est refusée avec un message français exploitable ; aucune création de paiement ne part d’un formulaire invalide |
| T04 | P05, P06, P07, P13 | Effectuer un paiement unique et confirmer l’inscription | Montant reçu correct, client retrouvé ou créé, achat associé, accès établi, e-mail cohérent avec le résultat |
| T05 | P05 | Répéter la vérification des moyens de paiement activés sur le compte Tara et disponibles en XAF | Chaque moyen est documenté avec son résultat ; aucun test non exécuté n’est présenté comme réussi |
| T06 | P05 | Soumettre ou recevoir une information de devise inattendue | La plateforme ne traite pas silencieusement un montant non XAF comme du XAF ; le cas est rejeté ou signalé selon le contrat fournisseur vérifié |
| T07 | P04, P06, P09 | Solder la première échéance du plan échelonné puis enregistrer un versement ultérieur en retard | Paiement reçu distinct du total du plan ; calendrier fixé à la date du paiement soldant la première échéance, selon l’intervalle du plan ; les échéances suivantes ne sont pas décalées par le retard ; solde attendu et échéances futures visibles |
| T08 | P06, P09 | Recevoir 200 XAF pour une première échéance attendue de 400 XAF | Les 200 XAF sont enregistrés ; l’échéance reste non soldée ; le calendrier ne démarre pas ; le montant partiel seul ne produit pas une erreur de rapprochement |
| T09 | P06, P09 | Recevoir un complément de 200 XAF attribué au même achat et à la première échéance de T08 | Les 400 XAF attendus sont couverts ; le calendrier démarre à la date de ce complément et non à celle du premier paiement partiel ; aucun versement n’est perdu ni compté deux fois |
| T10 | P06, P12 | Recevoir un paiement fait directement dans Tara | Rapprochement si les données sont suffisantes ; sinon paiement enregistré et erreur de rapprochement visible |
| T11 | P12, P14 | Corriger un paiement non attribué comme administrateur | Association explicite au bon utilisateur et à l’achat ; trace de l’opération ; aucune fabrication de paiement supplémentaire |
| T12 | P12, P14 | Tenter la même correction avec un gestionnaire | Correction d’attribution refusée selon le rôle réservé à l’administrateur |
| T13 | P07, P11 | Simuler un paiement confirmé puis un échec d’inscription ClickFunnels | Le paiement reste confirmé ; alerte exploitable ; aucune annonce d’accès réussi ; pas de reprise fonctionnelle automatique |
| T14 | P11 | Cliquer sur « Réessayer l’inscription » après résolution de l’échec | Accès établi et alerte résolue ; pas de nouveau paiement ; pas d’inscription en double |
| T15 | P08 | Inscrire manuellement un client après vérification d’un paiement | Paiement et inscription reliés au dossier correct ; action effectuée par un gestionnaire habilité |
| T16 | P08 | Offrir un accès sans paiement | Inscription créée et identifiée comme offerte ; aucun montant fictif ajouté aux paiements reçus |
| T17 | P09, P10 | Consulter une échéance en retard pendant le pilote | Retard visible ; absence de suspension automatique liée à l’itération 2 |
| T18 | P10 | Suspendre puis rétablir un accès depuis le tableau de bord | Changement effectivement appliqué au cours concerné dans ClickFunnels ; les autres cours du client restent inchangés |
| T19 | P01, P09 | Retrouver une inscription antérieure à la mise en service | Inscription gérable ; aucun historique de paiement ni dette créé par simple import |
| T20 | P13, P15 | Contrôler les e-mails avec les automatisations ClickFunnels désactivées pour le test | Nos messages partent indépendamment, sous la marque vendeur ; contenu compatible avec les états réellement atteints |
| T21 | P14 | Tester les opérations de gestion et la modification des connexions | Les gestionnaires autorisés disposent des fonctions confirmées ; les accès non autorisés sont refusés côté serveur |
| T22 | P06, P07, P11 | Répéter une confirmation fournisseur, un retour navigateur ou une action de reprise | Aucune double prise en compte du paiement ni duplication d’accès |
| T23 | P06 | Recevoir 600 XAF pour une échéance de 400 XAF | Montant réel conservé ; traitement du trop-perçu conforme à la décision D4 du cadrage ; aucune affectation future inventée |
| T24 | P02, P03, P07 | Changer de produit ou tenter de soumettre un prix / cours modifié depuis le navigateur | Le prix et le cours autorisés proviennent de la configuration serveur du produit et du plan sélectionnés |
| T25 | P03, P15 | Naviguer au clavier et agrandir le texte sur mobile | Choix, champs, erreurs et bouton utilisables ; informations financières lisibles ; bénéfices accessibles |

Les scénarios T08, T09 et T23 dépendent de la formalisation des règles de calcul et de trop-perçu. Leur présence dans ce plan ne vaut pas arbitrage de ces règles.

Pour T07, vérifier que les dates restent identiques avant et après l’enregistrement du versement tardif. Les scénarios T08 et T09 vérifient séparément que le calendrier attend le règlement complet de la première échéance.

## 3. Vérifications à réaliser séparément pour l’itération 2

- Rappels par e-mail aux dates définies.
- Suspension immédiate par défaut à l’échéance, puis comportement avec délai de grâce configuré.
- Paiement de régularisation attribuable : réactivation automatique lorsque le solde requis est couvert.
- Paiement ambigu : maintien dans le circuit de résolution manuelle.
- Annulation : traitement selon la signification du statut fournisseur et la règle métier retenue.

Le drip reste hors périmètre : ces essais vérifient l’accès au cours, pas la diffusion de ses leçons par notre plateforme.

## 4. Critères de sortie proposés

1. Tous les scénarios critiques applicables à l’itération 1 sont exécutés avec preuve.
2. Aucun défaut ouvert ne provoque perte de paiement, double crédit, attribution au mauvais client, accès au mauvais cours ou contournement d’une permission.
3. Les erreurs connues ont un état visible et un chemin de résolution accessible au bon rôle.
4. Les gestionnaires peuvent réaliser les opérations quotidiennes sans intervention d’un développeur.
5. Les scénarios dépendant d’un fournisseur non vérifié restent marqués bloqués et sont explicitement examinés avant toute décision d’ouverture.
6. La décision finale des gestionnaires est consignée, avec les éventuelles réserves, leurs responsables et leur date de levée.

Les seuils de charge et de performance font l’objet d’une décision séparée. Aucun test ne peut démontrer une capacité informatique « illimitée » ; la règle confirmée est l’absence de quota commercial.

## 5. Procès-verbal à compléter

| Élément | Valeur |
|---|---|
| Version de l’application / référence du code | À renseigner |
| Environnement et connexions testés | À renseigner |
| Gestionnaires ayant effectué la validation | À renseigner |
| Scénarios réussis / échoués / bloqués / non exécutés | À renseigner |
| Anomalies empêchant l’ouverture | À renseigner |
| Décisions métier restant ouvertes | À renseigner |
| Décision : ouverture acceptée, différée ou refusée | À renseigner |
| Conditions et responsable du suivi | À renseigner |
| Date de décision et validateurs | À renseigner |

Ce fichier prépare la recette. Il ne constitue pas un procès-verbal de tests réussis.
