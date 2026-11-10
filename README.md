# nearby_radio_detecteur

Application Ubuntu en Python 3 (3.12 testé) : détection des annonces Bluetooth
Low Energy avec Bleak, compteur sur 5 secondes, interfaces PySide6 et web,
classification indicative et historique local SQLite.

## Version 2 — parcours dans l’ordre de développement

1. **Présence** : expiration 5 s, RSSI configurable, sessions par adresse et
   mémoire temporaire 300 s. Les adresses ne sont jamais fusionnées.
2. **Calibration RSSI** : choisir une adresse connue, mesurer à 1, 3, 5 et 10 m,
   puis appliquer explicitement le seuil proposé.
3. **Graphique** : adresses actives échantillonnées chaque seconde, quinze
   minutes en mémoire, avec lacunes lorsque le scan est arrêté.
4. **Expériences** : nombre de référence déclaré, environnement contrôlé,
   durée et paramètres enregistrés, moyenne, maximum et écart relatif.
5. **Classification** : noms explicites, Appearance Bluetooth SIG et formats
   documentés, avec catégorie inconnue et justification des indices.
6. **Statistiques** : agrégats par minute dans SQLite, moyennes par heure,
   maximum simultané et heure la plus active sur les dernières 24 h.
7. **Performance** : purges mémoire regroupées, lectures d’identités par lots,
   séries bornées et statistiques conservées 30 jours.
8. **Alertes** : erreur du scanner, silence radio et seuil d’activité, avec
   journal borné des changements d’état.

La navigation du site donne accès à ces fonctions. L’interface Qt existante
conserve le scanner, le compteur, les réglages et l’historique ; les outils
V2 de calibration, graphiques et expériences sont proposés dans l’interface web.

### Calibration guidée

Identifier volontairement son appareil dans la liste, le placer à 1 m et
lancer « Mesurer », puis répéter à 3, 5 et 10 m. La mesure utilise aussi les
signaux de cette adresse sous le seuil RSSI. Un changement d’adresse empêche
le logiciel de savoir s’il s’agit encore du même appareil : réinitialiser le
profil et recommencer plutôt que fusionner deux adresses.

Chaque point affiche le nombre d’annonces, moyenne, médiane, écart-type et
extrêmes. Les quatre points doivent contenir au moins cinq annonces chacun
avant qu’un seuil puisse être proposé. Le seuil est la médiane à 10 m moins
3 dBm de marge ; c’est une règle locale explicite, pas une conversion fiable
RSSI/distance. Aucun seuil n’est appliqué automatiquement. Les points terminés
sont sauvegardés sans adresse dans SQLite ; une nouvelle instance démarre un
nouveau profil, sans mélanger automatiquement plusieurs appareils.

### Expérience contrôlée et estimation facultative

Indiquer un nombre réel d’appareils de référence, confirmer le contrôle de
l’environnement, puis lancer un essai de 10 à 3600 s. Le service échantillonne
les adresses actives environ chaque seconde. Les paramètres sont figés pendant
l’essai ; arrêter ou perdre le scanner annule l’expérience. Les échantillons
manquants ne sont pas inventés. Les résultats enregistrent le nombre réel
d’échantillons et les réglages, pas une liste d’adresses.

L’écart relatif est `abs(moyenne_adresses - référence) / référence * 100`.
Avec une référence de zéro, il reste indéfini et seul l’écart absolu est donné.
Cet écart ne démontre ni l’identification correcte des appareils, ni une
précision de comptage des personnes. Les autres émetteurs dans le couloir
peuvent fausser l’essai ; la confirmation du contexte est fournie par l’utilisateur.

Un essai complet avec moyenne et référence positives peut être choisi
explicitement pour une **estimation expérimentale** par rapport :
`adresses_actives / (moyenne_essai / référence_essai)`. Ce modèle local n’est pas
universel. Il est invalidé si le RSSI ou le délai de présence change. Sans essai
choisi, le site affiche « Non calibré » ; aucune estimation physique n’est
fabriquée. Le choix de l’essai est temporaire et peut être désactivé.

### Statistiques, filtres et alertes

Les moyennes horaires sont pondérées par les échantillons enregistrés, avec le
maximum observé et l’heure de moyenne maximale. Les données sont séparées par
seuil RSSI et durée de présence pour éviter de mélanger des configurations.
Le résumé historique est mis en cache pendant au plus 10 s. Les interruptions
ne sont pas enregistrées comme des zéros. Les agrégats de plus de 30 jours
sont purgés périodiquement, sans supprimer les autres historiques existants.
Les 1000 derniers résultats d’expérience et 100 profils de calibration sont
conservés. La série du graphique est limitée à 900 points.

Les filtres de fabricant/format, type et nouveauté concernent uniquement la
liste active ; le grand compteur reste le total des adresses actives. La durée
observée d’une ligne est le temps entre sa première et sa dernière annonce
dans la présence en cours. Les noms et métadonnées radio sont affichés comme
texte, jamais interprétés en HTML.

Les alertes configurables signalent une activité au moins égale au seuil
(par défaut 30 adresses) ou l’absence d’annonces pendant le délai choisi
(par défaut 30 s). Le silence radio peut être normal : il ne prouve pas une
panne. Les transitions sont journalisées sans répétition à chaque poll et
sans suivi individuel. Le scanner et les mesures continuent lorsque l’onglet
est fermé, tant que le serveur Python fonctionne. Aucun service système ou
appairage automatique n’est créé.

### Validation V2

```bash
.venv/bin/python -m unittest discover -s tests -v
```

QA navigateur optionnelle avec Playwright installé dans l’environnement de
développement :

```bash
.venv/bin/python tests/browser_v2.py
```

Le scénario utilise des observations synthétiques et des horloges de test :
il ne constitue pas une calibration ou une expérience physique dans le couloir.
Il couvre les quatre distances, l’expérience, les filtres, les estimations,
les statistiques, les alertes et la mise en page mobile. Les essais matériels
contrôlés restent à effectuer avec les appareils réels.

## Installation

Un adaptateur Bluetooth compatible BLE doit être disponible et activé. Le
service BlueZ et le bus D-Bus doivent fonctionner. Vérifier avec :

```bash
bluetoothctl show
```

Le résultat doit notamment indiquer `Powered: yes`. Si nécessaire, activer le
Bluetooth dans les paramètres Ubuntu. En cas de refus d’accès, vérifier les
autorisations Bluetooth de la session ; l’application ne nécessite pas de
lancer Python avec sudo.

Depuis le dossier du dépôt :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Si Ubuntu indique que `venv` manque, installer le paquet `python3-venv`.

## 1. Scanner dans le terminal

```bash
python -m bluetooth.scanner
```

Arrêter avec Ctrl+C. Pour un relevé limité :

```bash
python -m bluetooth.scanner --duration 10
```

Chaque annonce reçue affiche l’adresse observable, le nom lorsqu’il est
transmis, le RSSI en dBm, le type probable, les UUID de services annoncés et
les identifiants fabricant. Plusieurs lignes peuvent provenir du même appareil.
Le scanner terminal seul ne crée pas d’historique SQLite.
Le scanner ne se connecte pas aux appareils et ne demande aucun appairage.

La liste actuelle se rafraîchit chaque seconde et retire une adresse après
5 secondes sans annonce (au plus environ une seconde supplémentaire pour
l’affichage). Cela n’est pas une déconnexion Bluetooth : le logiciel écoute
les annonces. Un appareil qui émet rarement peut disparaître puis revenir ;
le délai reste réglable dans « Paramètres de présence ».

## 2. Compteur — solution A : présence glissante de 5 secondes

Le grand compteur et la liste Présence actuelle utilisent uniquement les
adresses dont la dernière annonce a moins de 5 secondes et satisfait le filtre
RSSI. Chaque nouvelle annonce rafraîchit le délai de la même adresse. À 30
secondes sans annonce, elle disparaît de la liste et du total lors de la
prochaine actualisation (au plus environ 1 seconde par défaut).

L’historique est indépendant : une adresse expirée y reste enregistrée mais
n’augmente pas le compteur actuel. La purge temporaire s’effectue 300 secondes après la dernière annonce,
à chaque nouvelle annonce, lecture ou entretien périodique du serveur.
Le site affiche « Adresses Bluetooth actives » et un délai d’expiration par
ligne, pour ne pas présenter le total comme un nombre certain d’appareils.
Deux adresses différentes d’un même appareil peuvent encore coexister pendant
cette fenêtre ; aucune fusion fondée sur le RSSI ou la marque n’est effectuée.


`compteur/counter.py` conserve la dernière observation par identifiant, sans
additionner les annonces répétées. Une observation expire à 5 secondes ; une
nouvelle annonce rafraîchit sa présence. Le temps utilisé est monotone.
Le compteur est utilisé dans le thread de l’interface.

## 3. Interface et 4. Assemblage

```bash
python main.py
```

La fenêtre affiche le compteur et la liste (nom, adresse observable, RSSI,
type probable, ID interne, apparitions et reconnaissance). Un onglet présente
les 100 derniers identifiants de l’historique, leurs premières et dernières
détections en UTC. Les infobulles détaillent les indices et l’ID complet.
Démarrer lance le scanner dans un thread séparé. Arrêter interrompt le scan ;
les observations restantes expirent normalement. Un nouveau démarrage efface
le relevé courant, mais conserve l’historique SQLite. L’affichage est actualisé chaque seconde par défaut. La fermeture
attend l’arrêt du scanner sans bloquer la boucle graphique. Une erreur radio
est affichée dans la fenêtre et permet de retenter un démarrage.

## Interface web locale

Depuis le dossier du dépôt, lancer :

```bash
.venv/bin/python -m web.server
```

Ouvrir **http://127.0.0.1:8765** dans le navigateur de ce même ordinateur.
Le tableau de bord propose le compteur, la liste des appareils (nom, adresse,
RSSI, type probable, reconnaissance et âge du dernier signal), les boutons Démarrer/Arrêter et le filtre RSSI
initialement réglé à −80 dBm. L’affichage se rafraîchit chaque seconde par défaut.
Le curseur propose les seuils de −127 à 0 dBm. L’onglet Historique local
affiche 25 identifiants par page, avec dates, UUID interne et apparitions.
Les infobulles expliquent les indices de classification.

Pour choisir un autre port :

```bash
.venv/bin/python -m web.server --port 8766
```

La détection utilise l’adaptateur du PC qui exécute le serveur. Le serveur
écoute uniquement sur `127.0.0.1`, sans exposition réseau ni publication cloud.
Les commandes de contrôle sont protégées par un jeton de session en mémoire et
une vérification de l’origine HTTP. Aucune bibliothèque web supplémentaire
n’est nécessaire. Ne pas placer ce serveur local derrière un proxy public.

Fermer un onglet ne coupe pas le scan : utiliser Arrêter ou terminer le serveur
avec Ctrl+C. Les onglets ouverts partagent le même scanner et le même seuil.
En cas de perte de connexion, l’interface signale que ses données ne sont plus
actualisées et tente de se reconnecter. Éviter d’exécuter simultanément les
interfaces Qt et web pendant un essai matériel.

## Marque, matériel probable et reconnaissance

Dans les vues web Présence actuelle et Historique local, la colonne
« Nom de l’appareil » réunit la marque et le type (exemple : Apple · Smartphone).
Le nom Bluetooth annoncé reste disponible dans l’infobulle. Elle est déduite d’un nom explicite ou
d’un petit sous-ensemble local des Company Identifiers Bluetooth SIG (Apple,
Samsung, Google, Microsoft, Sony et Lenovo). Le nom peut aussi reconnaître
quelques marques supplémentaires. Les autres identifiants restent inconnus.
Les noms OnePlus Buds et leurs variantes préfixées (`LE_OnePlus_Buds_Pro_2`)
sont reconnus comme écouteurs OnePlus ([fiche officielle](https://www.oneplus.com/us/oneplus-buds-pro-2)).
Les AirPods Max sont distingués des écouteurs AirPods, et les noms iPad,
Galaxy Z Fold/Flip et PC_JEAN sont également pris en compte.
Lorsqu’un code société est le seul indice, le site affiche « Signal au format
Apple/Microsoft/… » avec « type non annoncé » si le matériel reste indéterminé.
Cela ne confirme ni la marque du produit physique ni son propriétaire.
Les indices contradictoires affichent « Marque inconnue ». Un ID société n’est
pas une preuve du fabricant du produit final et n’impose jamais son type.
La marque est recalculée à la lecture des anciennes entrées ; aucune migration
ni modification de leurs UUID ou dates n’est nécessaire.

Quand BlueZ fournit le champ standard Appearance, le scanner le récupère sans
connexion GATT et le conserve comme indice (téléphone, ordinateur, montre,
écouteur ou casque). Les contradictions entre indices restent inconnues.
Référence : [valeurs Bluetooth SIG Appearance](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Assigned_Numbers/out/en/index-en.html).

`bluetooth/identifier.py` propose smartphone, montre connectée, écouteurs,
ordinateur, casque audio ou appareil inconnu. Les préfixes BLE comme
`LE_WH-1000XM6` sont pris en compte : ce nom est reconnu comme casque Sony
([fiche Sony](https://www.sony.fr/headphones/products/wh-1000xm6)).
Les entrées historiques sont réévaluées à la lecture avec les règles actuelles,
sans modifier leur UUID ni leurs dates. La classification utilise une petite liste
transparente de noms explicites (exemples : iPhone, Apple Watch, AirPods,
MacBook). Elle est toujours marquée « indicatif » : un nom est modifiable et
ne prouve pas le type. Un nom contradictoire reste inconnu.

Les UUID de services, IDs fabricant et longueurs des données fabricant
fournissent des indices supplémentaires expliqués dans le résultat.
Un service générique ou un ID fabricant seul ne suffit pas à classer un appareil :
un constructeur fabrique plusieurs catégories d’appareils. Les balises Microsoft CDP aux en-têtes et longueurs validés sont décodées
pour leur champ de type (ordinateur, téléphone, tablette ou console). Un
identifiant Microsoft seul ne suffit pas. Les signaux Apple anonymes restent
inconnus lorsque leurs annonces ne permettent pas de distinguer téléphone,
montre et ordinateur. Aucun décodage
propriétaire non documenté ni rapprochement par empreinte n’est effectué.

`database/storage.py` attribue un UUID interne par adresse observable normalisée.
Le même identifiant conserve cet UUID après redémarrage. Deux adresses différentes
restent deux entrées, même si leurs noms et annonces sont identiques. Une adresse
réutilisée peut aussi désigner un autre appareil physique : « Déjà observé »
signifie uniquement que le même identifiant a eu une apparition antérieure.

Une apparition est une période d’annonces successives, séparée de la précédente
par au moins 5 secondes sans annonce (délai configurable). Un redémarrage
rapide du scan ne crée pas d’apparition supplémentaire.
Les annonces répétées à moins de 5 secondes ne créent pas de nouvelles
apparitions. Les horodatages persistés utilisent l’heure système en UTC ; le
compteur de présence utilise toujours une horloge monotone.

### Historique SQLite

La base est créée automatiquement au premier lancement d’une interface :

```text
~/.local/share/nearby_radio_detecteur/history.sqlite3
```

Si `XDG_DATA_HOME` est défini, ce dossier remplace `~/.local/share`.
SQLite appartient à la bibliothèque standard Python : aucune dépendance à ajouter.
Les interfaces Qt et web partagent cette base. Éviter leurs scans simultanés :
chaque session distincte peut créer une apparition supplémentaire.

L’historique conserve l’adresse observable, le nom, le type indicatif et ses
indices, les IDs fabricant (sans blocs binaires bruts), les UUID de services,
les dates et périodes d’apparition. **Il inclut tous les signaux BLE reçus,
même ceux masqués par le filtre RSSI de la vue actuelle.** Il ne s’efface pas
avec Arrêter et ne comporte pas de purge automatique. Pour le réinitialiser,
arrêter toutes les instances, puis supprimer la base et ses éventuels fichiers
`history.sqlite3-wal` et `history.sqlite3-shm`. Une nouvelle base attribuera de
nouveaux UUID internes. Les fichiers SQLite sont exclus de Git.

## Adresses Bluetooth et préfixes MAC

Une adresse MAC Bluetooth n’est ni une adresse e-mail ni une adresse IP.
BlueZ indique si l’adresse observée est publique ou aléatoire ; le scanner
récupère cette information dans les détails du backend BlueZ de Bleak 3.1.
En cas de données absentes ou d’un autre backend, le type reste inconnu.
Les bits de l’adresse seuls ne servent jamais à décider qu’elle est publique.

La colonne d’adresse affiche ce type et, uniquement pour une adresse publique,
l’organisation associée aux 24 premiers bits dans notre extrait local du
[registre IEEE MA-L](https://standards-oui.ieee.org/oui/oui.csv). L’extrait du
9 novembre 2026 couvre certains fabricants courants, sans couvrir tous les
préfixes ni les registres MA-M/MA-S. Les adresses ne sont pas envoyées à un
service de recherche externe. Intel ou Realtek peuvent désigner un composant
réseau, pas la marque du PC ; aucune catégorie de matériel n’est déduite du
préfixe. Une marque d’appareil connue peut compléter les indices absents,
mais ne distingue pas iPhone, Mac ou Apple Watch.

Pour une adresse aléatoire, aucun fabricant n’est cherché dans le registre,
même si le préfixe coïncide avec celui d’Apple. Les anciens enregistrements sans
type sont marqués inconnus jusqu’à une nouvelle observation. Les adresses
privées ne sont pas fusionnées pour tenter de suivre un appareil.

Sources : [confidentialité Bluetooth Apple](https://support.apple.com/en-qa/guide/security/sec82597d97e/web),
[API BlueZ Device1](https://bluez.readthedocs.io/en/latest/device-api/) et
[identifiants IEEE](https://standards-support.ieee.org/hc/en-us/articles/4888705676564-Guidelines-for-Use-of-Extended-Unique-Identifier-EUI-Organizationally-Unique-Identifier-OUI-and-Company-ID-CID).

## Paramètres et compteurs de présence

Les valeurs partagées par défaut figurent dans `config.py` :

| Paramètre | Défaut |
| --- | --- |
| RSSI minimum actif | −80 dBm |
| Expiration de présence | 5 s |
| Conservation temporaire depuis la dernière annonce | 300 s |
| Actualisation des interfaces | 1 s |
| Période des sessions observées | 300 s |

Le site propose « Paramètres de présence » et une sélection de période pour
les sessions (5 min, 15 min, 1 h). L’interface Qt dispose aussi de ces réglages.
Ils s’appliquent à l’instance courante, sans réécriture de fichiers ou modification
rétroactive des anciennes sessions. Un redémarrage du programme reprend les
valeurs passées en ligne de commande, ou les défauts.

```bash
.venv/bin/python -m web.server --rssi -80 --presence-seconds 5 --retention-seconds 300 --refresh-seconds 1 --period-seconds 300
```

Les mêmes options sont acceptées par `python main.py`. La conservation doit
être au moins égale à la présence ; les paramètres invalides sont refusés sans
application partielle.

- **Adresses actives** : annonce récente et dernier RSSI au moins égal au seuil.
- **Sessions observées** : sessions commencées dans la période choisie, pour
  toutes les annonces reçues, même hors filtre RSSI.
- **Adresses expirées** : adresses sans annonce depuis le délai de présence,
  encore en mémoire temporaire ; elles disparaissent de ce compteur à 300 s
  d’inactivité par défaut. Ce compteur ne comprend pas tout l’historique.

Les adresses récentes sous le seuil RSSI sont indiquées séparément comme
filtrées : elles ne sont ni actives ni expirées. Chaque nouvelle annonce garde
une seule entrée temporaire par adresse. Le début de la période de présence
et la dernière annonce sont suivis avec une horloge monotone.

La mémoire temporaire est purgée automatiquement même sans navigateur ouvert
(le serveur effectue un entretien périodique) et après arrêt du scan tant que
l’interface reste ouverte. SQLite conserve les agrégats par adresse et période
de présence : dates, nombre de sessions et total d’annonces, sans paquet brut
ni ligne par annonce. La purge temporaire ne supprime pas ces statistiques.
Les anciens agrégats sont préservés, sans fusion des adresses privées ni
recalcul des apparitions historiques créées par les versions précédentes.

## Colonne des arrivées récentes (10 secondes)

La colonne latérale « Détections récentes » affiche le nom Bluetooth annoncé,
ou un libellé marque/type et une partie de l’adresse lorsque le nom manque.
Une carte apparaît au premier signal répondant au seuil RSSI d’une nouvelle
présence, y compris le retour après le délai d’absence. Les annonces répétées
ne prolongent pas ses 10 secondes d’affichage. Le navigateur retire la carte
avec son propre minuteur, même pendant une interruption réseau. Sur petit écran,
la colonne se place au-dessus du tableau de bord.

Il ne s’agit pas d’une connexion Bluetooth ni d’un nom de propriétaire deviné :
« Téléphone de Jean » ne peut être affiché que si ce nom est transmis. Les
alertes n’altèrent pas le compteur de présence ou les statistiques historiques.

## Nouvelles détections

L’onglet en haut à droite affiche le nombre d’adresses enregistrées pour la
première fois pendant la période sélectionnée (5 minutes par défaut), puis
les 100 plus récentes, triées par première détection décroissante. Il inclut
les signaux hors filtre RSSI, comme l’historique. Un badge « Nouveau » repère
aussi ces adresses dans la présence actuelle. Les données s’actualisent au
même rythme que les compteurs, même si cet onglet n’est pas ouvert.

Une nouvelle annonce ou un retour d’une adresse déjà enregistrée ne réinitialise
pas sa première détection. Le badge compte des nouveautés pendant une fenêtre
glissante, pas des notifications non lues : ouvrir l’onglet ne l’efface pas.
Une nouvelle adresse privée peut être un appareil physique déjà présent.
Aucune connexion ni aucun appairage Bluetooth n’est initié par cet onglet.

## Couleurs de fréquence

Dans les deux tableaux web, la bordure et le badge de fréquence suivent le
nombre d’apparitions : vert 1–2, jaune 3–5, orange 6–10, rouge 11–20, violet
au-delà de 20. Le libellé accompagne la couleur pour rester lisible sans
perception des couleurs. Les couleurs sont réévaluées à chaque actualisation
(1 seconde par défaut dans les deux vues).

« Détections totales » additionne les annonces enregistrées, tandis
qu’« Apparitions » compte les retours après 5 secondes sans
annonce. Une annonce supplémentaire durant la même apparition augmente le
total des détections sans faire évoluer la couleur. Il ne s’agit pas d’un
nombre de passages de personnes. Ces totaux sont calculés depuis les données
SQLite existantes, sans remise à zéro des anciens agrégats.

## Portée approximative de 10 mètres

La fenêtre active un filtre RSSI réglé initialement à **−80 dBm**. Ce seuil
est un choix de départ non calibré, pas une équivalence entre RSSI et mètres.
Le champ « Signal minimum » permet de le modifier pendant la détection :
une valeur plus proche de zéro rend le filtre plus strict.

Pour calibrer, placer un appareil qui diffuse des annonces BLE à 10 mètres,
observer son RSSI dans le scanner terminal non filtré, puis ajuster le seuil
dans la fenêtre. Refaire le relevé avec plusieurs appareils et positions.
Les murs, les puissances d’émission et les interférences rendent la limite
imprécise : des appareils proches peuvent être exclus et des appareils plus
éloignés inclus.

Seuls les identifiants dont le dernier signal reçu atteint le seuil sont
présents dans le compteur et la liste. Un nouveau signal plus faible les
masque dès l’actualisation suivante ; sans nouvelle annonce, ils expirent
après 5 secondes. Les signaux sans RSSI sont exclus. Un changement du seuil
réévalue les dernières observations encore récentes. Le scanner du terminal
reste non filtré pour aider à la calibration.

## Limites

Le compteur mesure des identifiants BLE récemment observés, pas des personnes
ni un nombre exact d’appareils physiques. Les adresses peuvent changer et tous
les appareils ne diffusent pas d’annonces BLE. Activer le Bluetooth d’un
téléphone ne garantit pas sa détection. Le Bluetooth classique seul n’est pas
couvert. Le RSSI est affiché sans conversion en distance.

Aucune statistique de passage ni détection Wi-Fi n’est intégrée. L’historique
reste sur ce PC, sans envoi automatique vers un service externe. Les sorties
du scanner terminal ne sont sauvegardées que si l’utilisateur les redirige.

## Validation

```bash
python -m unittest discover -s tests -v
```

Les tests utilisent des observations et un scanner simulés, sans accès radio.
Ils couvrent le dédoublonnage, l’expiration, le cycle démarrage/arrêt, la fermeture
et les erreurs. L’API web est testée avec un scanner simulé : cycle de
contrôle, filtrage, requêtes invalides et refus des origines/hôtes non autorisés.
La classification et la persistance sont testées avec données synthétiques :
absence d’indices, ambiguïtés, retour après expiration, nouvelle session,
réouverture d’une base, stabilité des UUID et absence de fusion d’adresses.
Les tests utilisent une base temporaire ou en mémoire, jamais l’historique
utilisateur. L’interface Qt est testée avec le moteur Qt hors écran. Les tests
réels avec plusieurs téléphones restent à effectuer sur place.

Références : [scanner Bleak](https://bleak.readthedocs.io/en/latest/api/scanner.html),
[données d’annonce Bleak](https://bleak.readthedocs.io/en/latest/backends/index.html#bleak.backends.scanner.AdvertisementData)
et [threads Qt](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html).
Les consignes du dépôt se trouvent dans `AGENTS.md`.
