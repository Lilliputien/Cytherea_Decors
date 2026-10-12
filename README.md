# Cytherea — Décors

Pièces de décor imprimables en 3D pour la campagne narrative Warhammer 40,000 « Cytherea — Le Nœud de Résonance ».
Elles sont générées sur demande depuis l'Atelier des cartes du site, aux cotes exactes des cartes (règles V11).

## Organisation

```
pieces/<lieu>/<piece>/v<N>/   STL, rendu.png, lot.zip (tous les STL de la version)
cartes/<partie>/              apercu-table.png, liste-impression.md, designs.json
outils/cytherea_terrain.py    générateur Blender
outils/verifier_stl.py        contrôle : étanchéité, cotes, surplombs
```

- **Par lieu** : un dossier par lieu de la page Système, où chaque pièce est réutilisable sur toutes les cartes.
- **Par partie** : `cartes/` garde l'aperçu 3D et la liste d'impression de chaque carte ; le site (Atelier → Pièces → Par partie) indique les pièces et les longueurs utilisées.
- Chaque version d'une pièce est conservée (`v1`, `v2`…).

Ce dépôt est public : il ne contient que des pièces originales générées. Aucun fichier acheté, même recoupé, n'y est déposé.

## Pièces disponibles

| Lieu | Pièce | Version | Réglages |
|---|---|---|---|
| Strates Basses | mur de tombe | v1 | gravé, dégâts légers, aimants 6 × 3 |
| Strates Basses | porte blindée | v1 | sobre, dégâts légers, aimants 5 × 2 |
| Strates Basses | portail de tombe | v1 | sobre, sans dégâts, aimants 5 × 2, autoportant 5″ |
| Strates Basses | pilier | v1 | gravé, dégâts légers |
| Strates Basses | pylône | v1 | gravé, dégâts légers |
| Strates Basses | sarcophage | v1 | gravé, dégâts légers |
| Strates Basses | pyramide | v1 | sobre, dégâts forts |

## Impression

FDM, sans supports, pièces debout. Remplissage conseillé : 10 à 15 % (8 à 10 % pour la pyramide, pleine).
Bordure (brim) conseillée pour les murs fins (0,5″).
Aimants néodyme aux extrémités des murs, des cadres de portes et des portails, selon la pièce :
6 × 3 mm (logements 6,3 × 3,3) ou 5 × 2 mm (logements 5,3 × 2,3). Ils sont aux mêmes hauteurs sur toutes les pièces
et s'attirent d'une taille à l'autre : garder la même polarité partout.
Les battants (`…_porte_a-plat.stl`) s'impriment à plat et glissent dans leur cadre par le haut.

## Générateur

Blender 4.2 ou plus récent (ou le module Python `bpy`).

    blender --background --python outils/cytherea_terrain.py -- --kit --out kit
    blender --background --python outils/cytherea_terrain.py -- --layout carte.json --designs designs.json --out carte --render
    python3 outils/verifier_stl.py carte

`carte.json` est l'export JSON de l'Atelier ; `designs.json` donne, par type de décor, le nom de la pièce et ses réglages
(`style` : base, sobre, grave ; `degats` : aucun, leger, fort ; `aimants` : 6x3, 5x2, aucun).
Options : `--bed 220` (plateau en mm, les murs plus longs sont coupés), `--render` (rendus Cycles).
