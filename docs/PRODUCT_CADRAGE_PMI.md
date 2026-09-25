# Cadrage produit — Intermédiaire Tara Money / ClickFunnels en marque blanche

Version : 0.1 — brouillon consolidé des entretiens, à relire avant d’en faire une référence de réalisation.

Premier utilisateur : Valued Haircare & Monafrolibre.

Jalon convenu : validation interne le dimanche 20 septembre 2026. L’ouverture aux clients intervient après accord des gestionnaires de Valued ; aucune date d’ouverture publique n’a été fixée.

## 1. Finalité et méthode de cadrage

Créer une plateforme intermédiaire qui permet à un vendeur utilisant ClickFunnels de recevoir des paiements via Tara Money, de les attribuer au bon client et au bon achat, puis de gérer les inscriptions et les accès aux cours. Les pages et les e-mails produits par la plateforme portent la marque du vendeur.

Le besoin exprimé est de faciliter l’encaissement des clients au Cameroun et, à terme, dans d’autres marchés africains, notamment grâce à Orange Money. La perception d’un manque d’alternatives de paiement est le contexte fourni par le porteur du produit ; aucune étude de marché n’est présentée comme réalisée.

La plateforme intervient entre les systèmes existants. Elle n’héberge pas les cours, ne gère pas leur diffusion progressive et ne remplace pas Tara Money comme prestataire de paiement.

Ce dossier adapte les pratiques PMI à un produit livré par itérations : finalité, gouvernance, périmètre, échéancier, ressources et financement, parties prenantes, risques, qualité et acceptation. Le PMBOK Guide, huitième édition, présente ces domaines et leur adaptation au contexte. Ce document est un cadrage de travail, pas une certification ni une charte formellement signée. [Référence PMI](https://www.pmi.org/standards/pmbok).

Les exigences et les scénarios de recette sont reliés aux objectifs pour rendre les décisions vérifiables. Cette approche de traçabilité est décrite dans les ressources PMI sur la maîtrise des exigences. [Traçabilité des exigences — PMI](https://www.pmi.org/learning/library/mastering-project-requirements-planning-controlling-closing-5814).

### Statut des informations

- **Confirmé** : décision exprimée ou explicitement acceptée pendant les entretiens.
- **Proposé** : disposition recommandée pour rendre le pilote vérifiable ; à accepter lors de la revue.
- **À préciser** : décision non obtenue, hypothèse ou capacité technique restant à vérifier.

Une exigence confirmée décrit le produit attendu ; elle ne prouve pas que le code actuel la satisfait.

## 2. Objectifs et mesure de réussite

| ID | Objectif confirmé | Indicateur proposé pour la validation interne |
|---|---|---|
| O1 | Permettre les paiements via Tara Money | Chaque moyen activé sur le compte vendeur et utilisable en XAF est vérifié, ou sa vérification externe est explicitement documentée comme bloquée |
| O2 | Attribuer correctement les paiements dans la plateforme | Chaque paiement de recette est soit rattaché à un achat identifié, soit visible en erreur de rapprochement ; aucun paiement confirmé ne disparaît |
| O3 | Connecter les achats aux inscriptions ClickFunnels | Le bon client obtient l’accès au bon cours, ou l’échec apparaît dans le tableau de bord avec une action de reprise manuelle |
| O4 | Permettre les opérations manuelles | Les gestionnaires exécutent les inscriptions payées, les accès offerts, les suspensions et les réactivations prévues |
| O5 | Rendre les échéances suivables dès le pilote | Le tableau de bord restitue les versements reçus, les soldes attendus, les échéances futures et les retards des dossiers suivis depuis la mise en service |
| O6 | Présenter une expérience en marque blanche | Les pages et e-mails maîtrisés par la plateforme portent Valued Haircare & Monafrolibre, sans identité commerciale de l’intermédiaire |

La fiabilisation automatisée des paiements échelonnés est un objectif confirmé de deuxième itération. L’accueil d’autres vendeurs est un objectif confirmé ultérieur ; son modèle économique et sa date ne sont pas définis.

Les seuils chiffrés de performance, le taux de conversion visé et le volume journalier réel ne sont pas fixés. L’absence de quota commercial ne constitue pas un objectif de capacité informatique infinie.

## 3. Acteurs et responsabilités

| Acteur | Responsabilités confirmées |
|---|---|
| Client acheteur | Choisir un plan, fournir ses coordonnées, payer activement chaque échéance via Tara, contacter le service client si nécessaire |
| Gestionnaire Valued | Configurer les produits et les connexions Tara / ClickFunnels ; gérer les inscriptions ; offrir un accès ; suivre les échéances ; suspendre et rétablir un accès ; relancer une inscription échouée |
| Administrateur | Associer manuellement à un utilisateur un paiement en erreur de rapprochement ; résoudre son rattachement à l’achat concerné |
| Gestionnaires Valued chargés de la recette | Réaliser la validation interne et donner l’accord d’ouverture |
| Tara Money | Exécuter les paiements et fournir les informations permettant d’en connaître le résultat |
| ClickFunnels | Porter les cours et les inscriptions ; exécuter les changements d’accès ; gérer seul le drip configuré par le vendeur |
| Notre plateforme | Porter les produits, liens d’achat, plans, suivi des paiements et opérations d’inscription ; envoyer ses propres e-mails |

**Proposé :** conserver pour les opérations sensibles l’auteur, la date, le dossier concerné, le motif et le résultat. Une tentative échouée doit être distinguée d’une action effectivement appliquée.

**À préciser :** liste nominative des validateurs, responsable de la réalisation, attribution et retrait des rôles, autres privilèges propres à l’administrateur. La correction d’un paiement non attribué n’est pas étendue aux gestionnaires par défaut : la décision exprimée réserve cette action à l’administrateur.

## 4. Répartition des données et vocabulaire

| Notion | Définition métier |
|---|---|
| Cours | Ressource importée depuis ClickFunnels, où le contenu et le drip restent gérés |
| Produit | Offre commerciale créée dans notre plateforme et associée à un seul cours |
| Plan de paiement | Modalités de règlement proposées pour un produit : versement unique ou plusieurs échéances |
| Lien produit | Lien d’achat partagé sur un site externe ; il ouvre le produit et ses plans disponibles |
| Achat / commande | Engagement d’un client envers un produit et un plan donné |
| Paiement reçu | Montant réellement reçu et confirmé par Tara, même s’il ne solde pas une échéance |
| Échéance | Date et solde attendu permettant de déterminer si le versement attendu est couvert |
| Inscription / accès | Droit du client sur le cours associé, avec son état effectif dans ClickFunnels |
| Accès offert | Inscription accordée sans paiement ; elle ne doit pas être présentée comme une vente payée |

**Confirmé :** un produit par cours, sans pack de plusieurs cours dans le pilote. Plusieurs plans peuvent appartenir au même produit. Les cours sont importés ; les produits ne sont pas importés comme des offres commerciales depuis ClickFunnels.

La représentation exacte d’un achat dans ClickFunnels reste à vérifier avec l’intégration disponible. Le besoin d’y refléter l’achat ne vaut pas confirmation de l’existence d’un objet de commande ou d’un endpoint particulier.

Les inscriptions déjà présentes dans ClickFunnels doivent être retrouvées et gérables. Leur historique financier n’est pas importé. Le suivi commence à la mise en service. Une inscription antérieure ne doit pas produire artificiellement un paiement, une dette ou un échéancier : la manière d’ouvrir un suivi pour un ancien client reste à préciser.

## 5. Périmètre par itération

### Itération 1 — Pilote interne puis ouverture autorisée

| ID | Exigence confirmée | Objectifs |
|---|---|---|
| P01 | Importer les cours et retrouver les clients / inscriptions ClickFunnels existants | O3, O4 |
| P02 | Créer les produits dans la plateforme, chacun associé à un cours | O1, O3 |
| P03 | Fournir un lien par produit, utilisable depuis un site externe | O1, O6 |
| P04 | Proposer le paiement en une fois et en plusieurs fois | O1, O5 |
| P05 | Accepter tous les moyens disponibles sur le compte Tara du vendeur, en XAF uniquement | O1 |
| P06 | Enregistrer les montants réellement reçus et rapprocher les paiements des clients et achats | O2 |
| P07 | Créer ou retrouver le client, refléter l’achat et ouvrir l’accès au cours selon le solde attendu | O3 |
| P08 | Permettre au gestionnaire d’inscrire un client après vérification d’un paiement ou de lui offrir un accès | O4 |
| P09 | Afficher paiements, solde restant, échéances à venir et retards pour un suivi manuel | O5 |
| P10 | Suspendre ou rétablir manuellement l’accès depuis le tableau de bord | O4, O5 |
| P11 | Afficher un échec d’inscription ClickFunnels et permettre une nouvelle tentative manuelle | O3, O4 |
| P12 | Signaler une erreur de rapprochement et permettre sa correction par l’administrateur | O2, O4 |
| P13 | Envoyer les e-mails indépendamment de ClickFunnels | O3, O6 |
| P14 | Gérer les permissions des collaborateurs selon leurs rôles | O4 |
| P15 | Présenter pages et e-mails sous la marque Valued Haircare & Monafrolibre | O6 |

**Exigences de parcours déjà exprimées :** interface client en français ; prénom, nom, e-mail et téléphone obligatoires ; mockup au-dessus de la liste des bénéfices ; plans avant coordonnées, récapitulatif et bouton ; Inter, texte lisible et composition compacte. Ces éléments sont des exigences UX, pas des preuves de conformité du rendu actuel.

Le client passe du lien produit à notre checkout, puis à la page de paiement Tara. Le retour du navigateur et la confirmation serveur du paiement sont deux événements distincts. La page de retour doit refléter l’état connu du paiement et de l’inscription, sans annoncer un accès qui n’a pas été établi. Il s’agit d’une exigence de cohérence proposée pour la recette.

### Itération 2 — Suivi automatisé des échéances

- Rappels par e-mail avec possibilité de payer activement la prochaine échéance.
- Suspension immédiate par défaut à l’échéance impayée, avec délai de grâce configurable.
- Prise en compte de l’annulation d’un paiement selon une règle métier à préciser.
- Réactivation automatique lorsque le paiement confirmé peut être attribué sans ambiguïté et que le solde requis est couvert.
- Intervention du service client et réactivation manuelle lorsque le rapprochement automatique est impossible.

Le client peut payer directement via Tara, sans emprunter le lien d’e-mail. Cette possibilité doit être prise en compte dans le rapprochement. Les prélèvements automatiques ne font pas partie de la solution décrite : selon le besoin exprimé, le client doit effectuer chaque paiement lui-même.

### Évolutions ultérieures, sans date engagée

- Traitement et affectation des excédents aux prochaines échéances.
- Ouverture à plusieurs vendeurs utilisant Tara Money et ClickFunnels.
- Configuration de la marque et des connexions propres à chaque vendeur, avec séparation de leurs données et de leurs accès.

L’ordre entre le traitement des excédents et l’ouverture à plusieurs vendeurs n’a pas été décidé.

### Exclusions confirmées

- Gestion du drip, du contenu pédagogique et de son calendrier de diffusion.
- Remboursements depuis la plateforme pendant le pilote.
- Import de l’historique financier antérieur à la mise en service.
- Packs donnant accès à plusieurs cours.
- Autres devises que le XAF dans le pilote.
- SMS et WhatsApp comme canaux de rappel du pilote.
- Automatisation du recouvrement et des suspensions dès l’itération 1.

Un CRM généraliste, un hébergement de cours, un abonnement SaaS facturé aux vendeurs et des fonctions comptables complètes ne sont pas des exigences recueillies.

## 6. Règles métier des paiements et des accès

### Enregistrement et validation

1. Un paiement reçu est enregistré à son montant réel. Il n’est pas remplacé par le montant théorique du plan.
2. La validation dépend du solde attendu pour l’échéance. Un paiement inférieur est conservé et n’est pas une erreur par nature.
3. Tant que le solde attendu n’est pas couvert, l’échéance ne doit pas être présentée comme soldée. La formule exacte de cumul et d’affectation doit être fixée dans la spécification de calcul.
4. Le paiement d’une nouvelle échéance exige une action du client ; aucun prélèvement automatique n’est prévu.
5. Un paiement directement effectué via Tara doit être enregistré et rapproché si les informations disponibles le permettent ; sinon, il suit le traitement des erreurs de rapprochement.
6. Un paiement supérieur au montant attendu est possible. Son affectation aux échéances futures a été reportée. **Proposition pour le pilote :** conserver le montant exact et signaler l’excédent sans créditer automatiquement les futures échéances. Cette proposition reste à confirmer.
7. **Confirmé :** le règlement complet de la première échéance démarre et fixe tout le calendrier. La date de référence est celle du paiement qui permet de couvrir entièrement son solde attendu, en tenant compte des paiements partiels déjà attribués. Un premier paiement partiel est enregistré mais ne déclenche pas le calendrier. Les échéances suivantes sont calculées à partir de cette date et de l’intervalle du plan. Les paiements ultérieurs ne recalculent pas les dates : un versement en retard ne décale pas les échéances suivantes.

### Exceptions

| Situation | Résultat attendu |
|---|---|
| Paiement confirmé, inscription ClickFunnels échouée | Conserver le paiement confirmé ; alerte sur le tableau de bord ; gestionnaire déclenche « Réessayer l’inscription » |
| Paiement confirmé, utilisateur ou achat introuvable | État « Erreur de rapprochement » ; association manuelle par l’administrateur |
| Paiement partiel | Enregistrer le paiement ; évaluer le solde attendu sans déclencher une erreur de rapprochement du seul fait du montant |
| Accès offert | Inscrire sans inventer de paiement ni inclure l’opération dans les ventes payées |
| Suspension / réactivation manuelle | Appliquer l’action au cours concerné et afficher son résultat réel |

Après un échec d’inscription, la reprise fonctionnelle demandée est manuelle. Un mécanisme existant de nouvelle tentative automatique ne devra pas être conservé par simple héritage du code. Les éventuelles nouvelles tentatives techniques de transport sont une décision d’implémentation à distinguer de cette règle produit.

**Proposé pour l’intégrité :** distinguer trois états indépendants : état du paiement, état du rapprochement et état de l’accès. La réception répétée d’une confirmation ou un clic répété sur une action ne doit ni compter deux fois l’argent ni créer deux inscriptions.

## 7. Parcours et expérience

### Achat depuis un site externe

Lien produit → choix du plan → coordonnées obligatoires → récapitulatif → paiement Tara → confirmation du paiement → rapprochement et contrôle du solde → création ou mise à jour de l’inscription → e-mail approprié.

Le succès du paiement et le succès de l’inscription sont présentés séparément si nécessaire. Un paiement en attente, partiel ou non attribué ne doit pas afficher arbitrairement une inscription réussie.

### Gestion quotidienne

Le gestionnaire consulte les paiements et échéances, retrouve un client, vérifie son inscription, effectue une inscription manuelle payée ou offerte, suspend ou rétablit un accès et traite les échecs d’inscription. L’administrateur dispose de la correction d’attribution des paiements en erreur.

### E-mails et marque blanche

Les e-mails sont envoyés par notre système, indépendamment de ClickFunnels. Le pilote utilise la marque Valued Haircare & Monafrolibre. Les automatisations ClickFunnels ne sont pas une dépendance pour la notification envoyée par notre plateforme.

**À préciser :** matrice exacte des messages, déclenchement d’un e-mail après un accès offert ou une suspension, expéditeur, adresse de réponse et traitement des erreurs d’envoi. **Proposition :** différencier confirmation de paiement et accès effectivement ouvert, et ne pas réexpédier inutilement un message lors d’une nouvelle tentative.

La marque blanche confirmée concerne les pages et e-mails maîtrisés par notre plateforme. La personnalisation de la page hébergée par Tara et la disponibilité de domaines propres aux vendeurs ne sont pas présumées ; elles restent à vérifier et à décider.

## 8. Qualité et exigences transverses proposées

Ces critères complètent le besoin métier et doivent être approuvés comme critères de recette.

- Vérifier l’authenticité des confirmations selon le mécanisme disponible chez Tara ; le navigateur ne constitue pas une preuve de paiement.
- Conserver la référence externe du paiement et empêcher sa prise en compte multiple.
- Faire respecter les droits côté serveur, pas uniquement en masquant les boutons.
- Garder une trace exploitable des changements d’accès, reprises, cadeaux et corrections d’attribution.
- Ne pas exposer les secrets de connexion dans les pages, e-mails ou journaux accessibles aux utilisateurs.
- Rendre les erreurs visibles et actionnables sans confondre paiement reçu et accès accordé.
- Tester la lisibilité, le clavier, les erreurs de formulaire et la mise en page mobile.
- Prévoir sauvegarde et restauration des données nécessaires à l’exploitation du pilote.

**Confirmé :** aucun quota commercial sur le nombre de cours, produits ou paiements. **À dimensionner :** concurrence, débit, taille des historiques, délais de synchronisation, stockage et limites des prestataires.

## 9. Décomposition du travail et livrables

| Lot proposé | Livrable vérifiable |
|---|---|
| L1 — Cadrage et écarts | Présent dossier relu ; comparaison des exigences avec le code et les intégrations disponibles |
| L2 — Catalogue et checkout | Cours importés, produits créés, liens par produit, plans et checkout en marque blanche |
| L3 — Paiements et soldes | Réception, validation, enregistrement, rapprochement et visibilité des exceptions |
| L4 — Inscriptions et accès | Création / recherche client, inscription, actions manuelles et reprise d’échec |
| L5 — Exploitation | Tableau de bord, permissions, e-mails et historique des opérations |
| L6 — Validation interne | Scénarios exécutés par les gestionnaires, résultats et anomalies consignés, décision d’ouverture |

Cette décomposition ne constitue pas une estimation de charge. Le dépôt existant est un point de départ à évaluer ; aucune fonctionnalité n’est déclarée terminée sur la seule base de ce cadrage.

Les documents existants sur le refactoring du module de paiement traitent notamment de structure technique. Le présent dossier décrit la cible produit complète. Tout écart entre les anciens documents et une décision métier recueillie ici doit être explicitement arbitré avant de figer la feuille de route technique.

## 10. Échéancier et décision d’ouverture

| Jalon | Date / condition |
|---|---|
| Cadrage de référence | Après revue des décisions ouvertes et priorisation des écarts |
| Version candidate à la recette | Avant la session de validation interne ; date précise à établir après analyse des écarts |
| Validation interne | Dimanche 20 septembre 2026, par les gestionnaires Valued |
| Corrections et nouvelle vérification | Selon les résultats de la recette |
| Ouverture aux clients | Après accord des gestionnaires ; date non fixée |
| Itération 2 et accueil d’autres vendeurs | À planifier séparément |

La date cible de recette ne prouve pas que tous les écarts pourront être réalisés avant cette date. Une indisponibilité fournisseur ou une anomalie bloquante doit être présentée avec son effet sur le périmètre et le jalon, plutôt que masquée.

**Porte d’acceptation proposée :** tous les scénarios critiques du pilote passent ; aucun paiement de test confirmé n’est perdu ou compté deux fois ; les exceptions sont récupérables par le rôle prévu ; les gestionnaires savent exécuter les opérations quotidiennes. Le détail figure dans [la recette du pilote](PRODUCT_RECETTE_PILOTE.md).

## 11. Ressources et financement

Ressources nécessaires à confirmer : responsable de réalisation, gestionnaires testeurs, accès aux comptes Tara et ClickFunnels, environnement de validation, envoi d’e-mails, hébergement, sauvegarde et moyens de supervision.

Aucun budget, plafond de dépense, coût cible par paiement, engagement de disponibilité ou effectif de réalisation n’a été communiqué. Ils sont laissés ouverts ; aucune dépense fournisseur n’est autorisée par ce document.

L’environnement de recette et la possibilité d’utiliser des transactions de test doivent être vérifiés. Si des transactions réelles sont nécessaires, leurs montants et leurs responsables doivent être définis avant exécution. La décision « validation interne » ne signifie pas « ouverture commerciale ».

## 12. Registre initial des risques

Les niveaux ci-dessous sont une appréciation qualitative proposée, pas une mesure issue d’un audit.

| ID | Risque | Effet possible | Réponse proposée |
|---|---|---|---|
| R1 | Paiement direct Tara sans identifiant de rattachement suffisant | Paiement confirmé mais client ou achat inconnu | Vérifier les données réellement reçues ; erreur visible et correction administrateur |
| R2 | Capacités ClickFunnels insuffisantes pour une opération attendue | Achat payé, suspension ou réactivation non appliquée | Démontrer chaque opération requise sur un cours de test ; signaler les limites |
| R3 | Confirmations répétées ou reçues dans un ordre inattendu | Double crédit ou état incohérent | Références uniques et scénarios de répétition / reprise |
| R4 | Confusion entre ancien inscrit et nouvel achat | Création de dette ou de paiement fictif | Séparer import d’inscription et suivi financier à partir de la mise en service |
| R5 | Mauvaise visibilité des paiements partiels ou excédentaires | Solde erroné et accès inapproprié | Fixer la règle de calcul ; conserver les montants reçus ; différer l’affectation des excédents |
| R6 | E-mail non envoyé ou accès non ouvert après paiement | Client sans information claire | États distincts et erreurs visibles ; politique de reprise à préciser |
| R7 | Périmètre trop large pour le jalon interne | Recette incomplète | Mesurer les écarts, isoler les fonctions d’itération 2 et faire arbitrer les blocages |
| R8 | Ouverture future à plusieurs vendeurs sans séparation suffisante | Mélange de paiements, connexions ou données | Définir et tester l’isolation avant cette ouverture |

## 13. Décisions encore ouvertes

| ID | Point à résoudre | Statut / incidence |
|---|---|---|
| D1 | Plans libres ou catalogue de formules ; montant total réparti ou montants fixés par échéance | L’interprétation actuelle est « plans configurés par les gestionnaires ». Les exemples 1 / 3 / 5 fois et 30 jours ne sont pas des limites explicitement imposées |
| D2 | Heure limite et fuseau des échéances | Démarrage après règlement complet de la première échéance, à la date du paiement qui la solde ; calendrier ensuite fixe. Heure limite et fuseau restant à préciser |
| D3 | Affectation des paiements partiels à un achat et cumul du solde attendu | Principe validé, formule de calcul et identifiants de rattachement à formaliser |
| D4 | Présentation du trop-perçu pendant le pilote | Affectation future reportée ; conservation et signalement proposés, à confirmer |
| D5 | Suivi futur d’un client déjà inscrit avant la mise en service | Pas d’historique financier ; éventuelle saisie d’un solde d’ouverture non encore validée |
| D6 | E-mails exacts du pilote et reprise après échec d’envoi | Envoi indépendant confirmé ; événements, expéditeur et gestion des échecs à définir |
| D7 | Budget, équipe, capacité et environnement de recette | Non renseignés |
| D8 | Délai de grâce, calendrier de rappels, annulation et reprise du drip après réactivation | Itération 2 ; le drip reste sous responsabilité ClickFunnels |
| D9 | Personnalisation de domaines et de l’écran Tara ; offre aux autres vendeurs | Au-delà de la marque blanche de nos pages et e-mails ; décisions et faisabilité ouvertes |

## 14. Gouvernance des changements

**Proposition :** chaque nouveau besoin indique l’objectif servi, les exigences affectées, les opérations utilisateur modifiées, les dépendances, l’impact estimé sur le jalon et les scénarios de recette à mettre à jour. Les décisions de périmètre sont consignées avant de modifier la référence de réalisation.

Le porteur du produit arbitre les besoins métier selon une responsabilité à confirmer ; les gestionnaires Valued conservent le rôle confirmé de validation opérationnelle et d’accord d’ouverture. Une revue de ce brouillon doit distinguer acceptation du besoin, faisabilité de l’intégration et succès réel de la recette.
