# PAROL6 — logiciel PC

Interface Windows pour piloter le bras PAROL6 : capteurs, manette, suites de mouvements et programmes.

Le firmware est déjà dans la carte du robot. Ce PC parle au bras en USB. Le numéro de port COM change d’un ordinateur à l’autre.

Ce logiciel utilise l’API Windows. Il se lance sur un PC Windows.

Garde une main sur l’arrêt d’urgence. Le bras peut bouger dès qu’une suite ou la manette envoie une vitesse.

## Utilisation

Une fois l’installation faite, le logiciel s’ouvre par l’icône **PAROL6** sur le Bureau. Il n’y a pas de terminal.

À chaque clic, le raccourci récupère la dernière version sur GitHub, puis ouvre la fenêtre. Le numéro de version est écrit dans la barre du haut, à gauche du bouton **RENDRE MOU**, et dans le titre de la fenêtre. C’est ce numéro qu’on donne pour le support.

Les suites, les programmes et le mapping de la manette sont enregistrés dans `%APPDATA%\PAROL6` (`motion_library.json`, `pad_map.json`, `last_motion.json`). Une mise à jour du logiciel ne les remplace pas.

1. Branche le câble USB du robot sur ce PC, et la manette aussi. La manette est lue par Windows, pas par la carte.
2. Dans le logiciel, choisis le port **STM32 Virtual COM Port** (`COM3`, `COM5`, etc.). **Rafraichir** relit la liste si le câble vient d’être branché. Le nom exact est dans le Gestionnaire de périphériques, sous **Ports (COM et LPT)**.
3. Clique **Connecter**.
4. Ferme le moniteur série PlatformIO avant de connecter. Un port COM ne s’ouvre qu’une fois.

## Première installation

À faire une seule fois sur le PC.

1. Installe [Git pour Windows](https://git-scm.com/download/win).
2. Installe [Python 3.11](https://www.python.org/downloads/release/python-3119/). Sur le premier écran, coche **Add python.exe to PATH**.
3. Ouvre PowerShell :

```powershell
cd $env:USERPROFILE\Documents
git clone https://github.com/MoiOrg/parol6-gamepad-control
cd parol6-gamepad-control
```

4. Double-clique `PAROL6.vbs` dans ce dossier. La fenêtre s’ouvre, et l’icône **PAROL6** apparaît sur le Bureau. Ensuite, seul ce raccourci sert.

`pyserial` est optionnel. Sans lui, le logiciel ouvre le port via Windows. Pour une liste de ports plus claire :

```powershell
py -3.11 -m pip install pyserial
```

Si l’icône ne s’ouvre pas, le dernier essai de mise à jour est dans `%APPDATA%\PAROL6\update.log`.

## Fichiers du dépôt

| Fichier | Rôle |
| --- | --- |
| `PAROL6.vbs` | Raccourci : mise à jour, puis ouverture sans terminal. |
| `sensor_gui.py` | Interface. |
| `VERSION` | Numéro affiché dans la fenêtre. À changer quand tu publies une mise à jour. |
| `motion_library.json` | Suites de départ, copiées une fois dans `%APPDATA%\PAROL6`. |
| `pad_map.json` | Mapping de départ de la manette. |
| `last_motion.json` | Dernier enregistrement de départ. |
| `src/wiring_test.cpp` | Firmware de la carte. Utile seulement pour reflasher. |
| `platformio.ini` | Config PlatformIO, pour un reflash. |

Le `.gitignore` laisse de côté `.pio` et `__pycache__`.

## Reflasher la carte

À faire seulement si cette carte n’a pas encore ce firmware.

- Visual Studio Code avec l’extension PlatformIO.
- Un ST-Link et ses drivers.
- Le 24 V débranché pendant l’upload.
- Les bibliothèques pointées par `platformio.ini` : le dossier `PAROL6 control board test code/lib`, au même niveau que ce projet (`../PAROL6 control board test code/lib`). Sans ce dossier, la compilation échoue. Pour seulement piloter le bras, il n’est pas nécessaire.

Ouvre ce dossier dans PlatformIO et lance **Upload**. Au démarrage, le bras ne bouge pas tant qu’une commande, la manette ou une suite ne lui demande pas de bouger.
