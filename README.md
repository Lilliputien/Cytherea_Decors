# Cytherea — Décors

Pièces de décor imprimables en 3D pour la campagne narrative Warhammer 40,000 « Cytherea — Le Nœud de Résonance ».
Elles sont générées sur demande depuis l'Atelier des cartes du site, aux cotes exactes des cartes (règles V11).

## Organisation

```
pieces/<lieu>/<piece>/v<N>/   STL, rendu.png, lot.zip
outils/cytherea_terrain.py    générateur Blender
```

- **Par lieu** : un dossier par lieu de la page Système, où chaque pièce est réutilisable sur toutes les cartes.
- **Par partie** : c'est le site (Atelier → Pièces → Par partie) qui indique quelles pièces et quelles longueurs utilise chaque carte.
- Chaque version d'une pièce est conservée (`v1`, `v2`…).

Ce dépôt est public : il ne contient que des pièces originales générées. Aucun fichier acheté, même recoupé, n'y est déposé.

## Impression

FDM, sans supports, pièces debout. Remplissage conseillé : 10 à 15 %.
Aimants néodyme 6 × 3 mm (logements 6,3 × 3,3 mm) aux extrémités des murs, seuils et portails.
Les portes (`…_porte_a-plat.stl`) s'impriment à plat et glissent dans leur cadre par le haut.

## Générateur

Blender 4.2 ou plus récent.

    blender --background --python outils/cytherea_terrain.py -- --kit --out kit
    blender --background --python outils/cytherea_terrain.py -- --layout carte.json --out carte --render

`carte.json` est l'export JSON de l'Atelier. Options : `--bed 220` (plateau en mm), `--render` (rendus Cycles).
