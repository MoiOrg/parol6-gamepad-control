# PAROL6 — logiciel PC

Interface Windows pour piloter le bras PAROL6 : capteurs, manette, suites de mouvements et programmes.

Le programme qui tourne sur le PC est `sensor_gui.py`. Le firmware est déjà dans la carte du robot. Changer de PC ne demande pas de reflasher : le nouveau PC parle au bras en USB. Le numéro de port COM change d’un ordinateur à l’autre. Les suites et le mapping manette voyagent avec les fichiers du dépôt.

Ce logiciel utilise l’API Windows (port série et manette). Il se lance sur un PC Windows.

Garde une main sur l’arrêt d’urgence. Le bras peut bouger dès qu’une suite ou la manette envoie une vitesse.

## Fichiers du dépôt

| Fichier | Rôle |
| --- | --- |
| `sensor_gui.py` | Interface. C’est elle qu’on lance. |
| `motion_library.json` | Suites et programmes enregistrés. |
| `pad_map.json` | Mapping de la manette. |
| `last_motion.json` | Dernier enregistrement. |
| `src/wiring_test.cpp` | Firmware de la carte. Utile seulement pour reflasher. |
| `platformio.ini` | Config PlatformIO, pour un reflash. |

`platformio.ini` ignore déjà le dossier `.pio`. Ne versionne pas `.pio` ni `__pycache__` : ce sont des fichiers de compilation, régénérés sur place.

## Installer sur un autre PC

1. Installe [Git pour Windows](https://git-scm.com/download/win).
2. Installe [Python 3.11](https://www.python.org/downloads/release/python-3119/). Sur le premier écran, coche **Add python.exe to PATH**. Tkinter est inclus dans cet installeur.
3. Ouvre PowerShell et clone le dépôt :

```powershell
cd $env:USERPROFILE\Documents
git clone https://github.com/MoiOrg/parol6-gamepad-control
cd parol6-gamepad-control
```

4. Branche le câble USB du robot sur ce PC, et la manette aussi. La manette est lue par Windows, pas par la carte.

## Lancer

Dans le dossier du dépôt :

```powershell
python sensor_gui.py
```

La fenêtre s’appelle **PAROL6 — capteurs + manette**.

1. Ouvre le Gestionnaire de périphériques et regarde **Ports (COM et LPT)**. Note le port **STM32 Virtual COM Port** (`COM3`, `COM5`, etc.).
2. Dans le logiciel, choisis ce port. **Rafraichir** relit la liste si le câble vient d’être branché.
3. Clique **Connecter**. Le statut passe de « Deconnecte » à connecté.
4. Ferme le moniteur série PlatformIO avant de connecter. Un port COM ne s’ouvre qu’une fois : s’il est déjà pris, la connexion échoue.

`pyserial` est optionnel. Sans lui, le logiciel ouvre le port via Windows. Pour une liste de ports plus propre :

```powershell
pip install pyserial
```

## Reflasher la carte

À faire seulement si cette carte n’a pas encore ce firmware.

- Visual Studio Code avec l’extension PlatformIO.
- Un ST-Link et ses drivers.
- Le 24 V débranché pendant l’upload.
- Les bibliothèques pointées par `platformio.ini` : le dossier `PAROL6 control board test code/lib`, au même niveau que ce projet (`../PAROL6 control board test code/lib`). Sans ce dossier, la compilation échoue. Pour seulement piloter le bras, il n’est pas nécessaire.

Ouvre ce dossier dans PlatformIO et lance **Upload**. Au démarrage, le bras ne bouge pas tant qu’une commande, la manette ou une suite ne lui demande pas de bouger.
