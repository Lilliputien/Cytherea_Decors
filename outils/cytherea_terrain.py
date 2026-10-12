"""
CYTHEREA — Générateur de décors imprimables (Blender 4.2+ / 5.x)
================================================================

Fabrique des décors nécrons aux cotes exactes de l'Atelier des cartes
(catalogue decor_types, pouces), en STL millimétrés, imprimables en FDM
sans supports (pièces debout, surplombs ≤ 45°).

Pièces :
  - mur de tombe     (tomb_wall)  longueur et épaisseur au choix, hauteur 4,1″
  - seuil scellé     (door_tomb)  cadre + porte amovible qui glisse par le haut
  - porte blindée    (door_hive)  même principe, au style nécron, hauteur des murs
  - portail de tombe (gate)       autoportant, contreforts, voile amovible
  - niche de stase   (niche)      alcôve avec capsule de stase
  - pilier, sarcophage, pylône octogonal, pyramide (lisse ou à gradins)

Réglages d'une pièce (fiche « Personnaliser » de l'Atelier) :
  style   : base | sobre | grave      (gravé = colonnes de glyphes en creux)
  degats  : aucun | leger | fort      (éclats au sommet, coins mordus, fissures)
  aimants : 6x3 | 5x2 | aucun         (logements aux extrémités des murs, cadres)
Les dégâts sont tirés au hasard mais de façon reproductible (graine = design).

Utilisation
-----------
1. Le kit de base (8″, 4″, 2″, seuil, niche…) :
     blender --background --python cytherea_terrain.py -- --kit --out kit
2. Depuis une carte exportée de l'Atelier (bouton JSON) :
     blender --background --python cytherea_terrain.py -- --layout carte.json --out carte
   -> une pièce par taille réellement posée (murs trop longs pour le plateau
      coupés en parts égales), une liste d'impression et un rendu 3D de la table.
3. Avec des designs (réglages par type de décor) :
     ... -- --layout carte.json --designs designs.json --out carte --render
   designs.json : {"tomb_wall": {"slug": "mur-de-tombe", "seed": "…",
                   "params": {"style": "grave", "degats": "leger", "aimants": "6x3"},
                   "h_in": 4.1}, …}
   -> un dossier par design (STL + rendu.png), seuls les types listés sont générés.
4. Dans l'interface de Blender : onglet Scripting, ouvrir ce fichier,
   régler PARAMS_GUI en bas du fichier, puis Exécuter.
Options : --bed 220 (taille du plateau en mm), --render (rendus Cycles).

Repères : 1 pouce = 25,4 mm. Carte de l'Atelier : x vers la droite,
y vers le bas ; dans Blender, Y = -y (le haut du plan est au fond).
"""
import bpy, bmesh, math, os, sys, json, argparse, struct, random, zlib
from mathutils import Vector

IN = 25.4
CATALOG = {  # cotes du catalogue Supabase (pouces) : largeur, profondeur, hauteur
    "tomb_wall": (8.0, 1.2, 4.1),
    "door_tomb": (3.0, 1.0, 4.1),
    "door_hive": (3.0, 0.6, 4.1),   # posée dans les murs de tombe : hauteur des murs
    "niche": (3.0, 1.6, 4.0),
    "gate": (4.0, 1.0, 4.0),
    "pillar": (2.4, 2.4, 5.0),
    "sarco": (3.0, 1.4, 1.5),
    "pylon": (5.0, 5.0, 8.0),
    "pyramid": (10.0, 10.0, 6.0),
}
NAMES = {"tomb_wall": "mur-de-tombe", "door_tomb": "seuil-scelle", "door_hive": "porte-blindee",
         "niche": "niche-de-stase", "gate": "portail-de-tombe", "pillar": "pilier", "sarco": "sarcophage",
         "pylon": "pylone", "pyramid": "pyramide"}
# hauteurs du catalogue pour les décors non générés (blocs « à sourcer » du rendu)
PLACEHOLDER_H = {"ruin_l": 6, "ruin_box": 5, "wall": 3, "muraille": 6, "container": 3, "rubble": 1, "crater": 0.3,
                 "trench": 0.3, "sandbags": 1, "barricade": 1.5, "wire": 1, "wall_door": 3,
                 "tomb_wall_door": 4.1, "door_bunker": 2}
MAGNETS = {"6x3": (6.3, 3.3), "5x2": (5.3, 2.3)}   # logements (avec jeu) : diamètre, profondeur
SLOT_W, SLAB_T = 3.5, 3.0                          # rainure de la porte et épaisseur du battant
CREST = 16.0                                       # fronton d'un portail autoportant (mm)

# ---------------------------------------------------------------- réglages courants
DEFAULT_P = {"style": "base", "degats": "aucun", "aimants": "6x3", "panneaux": "rect", "led": "non"}
_P = dict(DEFAULT_P)
_rng = random.Random(0)

def set_params(params=None, seed="cytherea"):
    """Réglages appliqués aux pièces construites ensuite, et graine des dégâts."""
    global _P, _rng
    _P = dict(DEFAULT_P)
    for k, v in (params or {}).items():
        if v not in (None, ""):
            _P[k] = v
    _rng = random.Random(zlib.crc32(str(seed).encode("utf-8")))

def _engraved():
    return _P.get("style") in ("grave", "gravé")

def _plain():
    return _P.get("style") == "sobre"

def _dmg():
    return {"leger": 1, "léger": 1, "fort": 2}.get(_P.get("degats"), 0)

# ---------------------------------------------------------------- outils
def _scene_reset():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)

_MATS = {}
def mat(kind):
    """Matériaux de rendu : 'pierre' et 'gauss' (lumineux). Sans effet sur les STL."""
    if kind not in _MATS or _MATS[kind].name not in bpy.data.materials:
        m = bpy.data.materials.new(kind)
        m.use_nodes = True
        bs = m.node_tree.nodes.get("Principled BSDF")
        if kind == "asourcer":
            bs.inputs["Base Color"].default_value = (0.32, 0.3, 0.27, 1)
            bs.inputs["Roughness"].default_value = 0.9
        elif kind == "gauss":
            bs.inputs["Base Color"].default_value = (0.2, 0.9, 0.45, 1)
            bs.inputs["Emission Color"].default_value = (0.31, 1.0, 0.54, 1)
            bs.inputs["Emission Strength"].default_value = 4.0
        else:
            bs.inputs["Base Color"].default_value = (0.13, 0.17, 0.15, 1)
            bs.inputs["Roughness"].default_value = 0.62
            bs.inputs["Metallic"].default_value = 0.35
        _MATS[kind] = m
    return _MATS[kind]

def hull(name, pts, coll=None):
    """Objet convexe à partir d'une liste de points (mm). Un nom contenant
    '_g', '_fente' ou '_lum' reçoit le matériau lumineux de gauss."""
    bm = bmesh.new()
    for p in pts:
        bm.verts.new(p)
    ret = bmesh.ops.convex_hull(bm, input=list(bm.verts))
    junk = list({g for g in ret["geom_interior"] + ret["geom_unused"] if isinstance(g, bmesh.types.BMVert)})
    if junk:
        bmesh.ops.delete(bm, geom=junk, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat("gauss" if any(k in name for k in ("_g", "_fente", "_lum")) else "pierre"))
    ob = bpy.data.objects.new(name, me)
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob

def box(name, x0, x1, y0, y1, z0, z1, coll=None):
    return hull(name, [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], coll)

def prism_xz(name, poly, y0, y1, coll=None):
    """Prisme : polygone convexe dans le plan XZ extrudé de y0 à y1."""
    return hull(name, [(x, y, z) for (x, z) in poly for y in (y0, y1)], coll)

def prism_yz(name, poly, x0, x1, coll=None):
    return hull(name, [(x, y, z) for (y, z) in poly for x in (x0, x1)], coll)

def teardrop_x(name, x0, x1, y, z, d, coll=None):
    """Trou horizontal (axe X) en goutte d'eau : imprimable sans support."""
    r, pts = d / 2, []
    for i in range(16):
        a = 2 * math.pi * i / 16
        pts.append((y + r * math.cos(a), z + r * math.sin(a)))
    pts.append((y, z + r * 1.414))
    return prism_yz(name, pts, x0, x1, coll)

def _swap_xy(ob):
    """Échange x et y des sommets (pour reporter une découpe d'une face Y sur une face X)."""
    for v in ob.data.vertices:
        v.co.x, v.co.y = v.co.y, v.co.x
    return ob

def _collection(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c

def _apply_bool(ob, coll, op):
    if not coll.objects:
        return
    mod = ob.modifiers.new("b", "BOOLEAN")
    mod.operation = op
    mod.operand_type = "COLLECTION"
    mod.collection = coll
    mod.solver = "EXACT"
    if hasattr(mod, "material_mode"):
        mod.material_mode = "TRANSFER"
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    old = ob.data
    ob.modifiers.clear()
    ob.data = me
    bpy.data.meshes.remove(old)
    for o in list(coll.objects):
        bpy.data.objects.remove(o, do_unlink=True)

def build(name, parts, cuts):
    """parts[0] uni avec parts[1:], puis moins cuts. Renvoie l'objet final."""
    add, sub = _collection(name + "_add"), _collection(name + "_sub")
    base = parts[0]
    for p in parts[1:]:
        _relink(p, add)
    for c in cuts:
        _relink(c, sub)
    _apply_bool(base, add, "UNION")
    _apply_bool(base, sub, "DIFFERENCE")
    bpy.data.collections.remove(add)
    bpy.data.collections.remove(sub)
    bm = bmesh.new()
    bm.from_mesh(base.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(base.data)
    bm.free()
    base.name = name
    return base

def _relink(ob, coll):
    for c in ob.users_collection:
        c.objects.unlink(ob)
    coll.objects.link(ob)

def write_stl(ob, path):
    """STL binaire en mm, coordonnées locales posées sur z = 0."""
    me = ob.data
    me.calc_loop_triangles()
    tris = me.loop_triangles
    with open(path, "wb") as f:
        f.write(b"Cytherea - generated with Blender".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            n = t.normal
            f.write(struct.pack("<3f", n.x, n.y, n.z))
            for vi in t.vertices:
                v = me.vertices[vi].co
                f.write(struct.pack("<3f", v.x, v.y, v.z))
            f.write(b"\0\0")

# ---------------------------------------------------------------- détails communs
def _panel_cut(tag, x0, x1, z0, z1, face, sign, d):
    """Creux rectangulaire sur une face (sign = +1 face avant, -1 face arrière),
    haut chanfreiné à 45° pour imprimer debout sans support."""
    out, ins = face + 1, face - d
    pts = []
    for x in (x0, x1):
        pts += [(x, sign * out, z0), (x, sign * ins, z0), (x, sign * out, z1 + 1), (x, sign * ins, z1 - d)]
    return hull(tag, pts)

def _slit_cut(tag, xc, w, z0, z1, face, sign, d):
    return _panel_cut(tag, xc - w / 2, xc + w / 2, z0, z1, face, sign, d)

def _glyphs(tag, xc, z_top, z_bot, face, sign, d=0.7, scale=1.0):
    """Colonne de glyphes nécrons gravés (barres, points, traits), de z_top à z_bot."""
    cuts, z, k = [], z_top, 0
    while z > z_bot + 4 * scale:
        g = _rng.choice(("barre", "point", "paire", "trait", "barre", "croix"))
        s = scale
        if g == "barre":
            cuts.append(_panel_cut(f"{tag}_{k}", xc - 3 * s, xc + 3 * s, z - 1.2 * s, z, face, sign, d)); h = 1.2 * s
        elif g == "point":
            cuts.append(_panel_cut(f"{tag}_{k}", xc - 1 * s, xc + 1 * s, z - 2 * s, z, face, sign, d)); h = 2 * s
        elif g == "paire":
            for j, dx in enumerate((-2.2 * s, 2.2 * s)):
                cuts.append(_panel_cut(f"{tag}_{k}{j}", xc + dx - 1 * s, xc + dx + 1 * s, z - 2 * s, z, face, sign, d))
            h = 2 * s
        elif g == "trait":
            cuts.append(_panel_cut(f"{tag}_{k}", xc - 0.6 * s, xc + 0.6 * s, z - 6 * s, z, face, sign, d)); h = 6 * s
        else:  # croix : barre + trait court
            cuts.append(_panel_cut(f"{tag}_{k}a", xc - 2.5 * s, xc + 2.5 * s, z - 2.6 * s, z - 1.4 * s, face, sign, d))
            cuts.append(_panel_cut(f"{tag}_{k}b", xc - 0.6 * s, xc + 0.6 * s, z - 4 * s, z, face, sign, d)); h = 4 * s
        z -= h + 2.2 * s
        k += 1
    return cuts

def _magnets(tag, L, H, cuts, y=0.0):
    """Deux logements d'aimant à chaque extrémité (axe X), selon le réglage."""
    m = MAGNETS.get(str(_P.get("aimants", "6x3")).replace("×", "x"))
    if not m:
        return 0
    dia, depth = m
    for x_end in (0, L):
        x0, x1 = (-1, depth) if x_end == 0 else (L - depth, L + 1)
        for k, z in enumerate((24, H - 36)):
            cuts.append(teardrop_x(f"{tag}_aimant{x_end}_{k}", x0, x1, y, z, dia))
    return 4

def _up(pts, h=600):
    """Prolonge une découpe vers le haut : ses parois sont verticales ou tournées
    vers le haut, donc aucun surplomb (éclats imprimables sans support)."""
    return pts + [(x, y, z + h) for (x, y, z) in pts]

def chip(tag, cx, cy, cz, rx, ry, rz):
    """Éclat : bloc convexe irrégulier sous le point (cx, cy, cz), ouvert vers le haut."""
    pts = []
    for i in range(8):
        a = 2 * math.pi * i / 8 + _rng.uniform(-0.3, 0.3)
        f = _rng.uniform(0.55, 1.0)
        pts.append((cx + rx * f * math.cos(a), cy + ry * f * math.sin(a), cz - rz * _rng.uniform(0.35, 1.0)))
    pts.append((cx, cy, cz - rz))
    return hull(tag, _up(pts))

def crack(tag, u0, z0, length, face, sign, w=1.0, d=0.8, axis="y", zmin=8):
    """Fissure en zigzag descendant sur une face verticale (y = sign·face, ou x si axis='x')."""
    out, segs, u, z, k = [], [], u0, z0, 0
    while length > 0 and z > zmin:
        du, dz = _rng.uniform(-4, 4), _rng.uniform(4, 8)
        segs.append(((u, z), (u + du, z - dz)))
        u, z, length = u + du, z - dz, length - math.hypot(du, dz)
    for (a, b) in segs:
        pts = []
        for (uu, zz) in (a, b):
            for du in (-w / 2, w / 2):
                pts += [(uu + du, sign * (face + 1), zz), (uu + du, sign * (face - d), zz)]
        ob = hull(f"{tag}_{k}", pts)
        out.append(_swap_xy(ob) if axis == "x" else ob)
        k += 1
    return out

def _wall_dims(T):
    """Retrait du corps et chaperon, proportionnés à l'épaisseur (murs fins de l'Atelier)."""
    b = T / 2
    s = min(3.0, max(1.0, T * 0.1))
    top = b - 7 if b >= 12 else b * 0.35
    return b, s, top

# ---------------------------------------------------------------- pièces
def _wall_core(L, T, H, tag):
    """Plinthe, corps et chaperon chanfreiné d'un mur, de x = 0 à L."""
    b, s, top = _wall_dims(T)
    plinth = box(tag + "_plinthe", 0, L, -b, b, 0, 6)
    body = box(tag + "_corps", 0, L, -(b - s), b - s, 5, H - 10)
    o = b - s / 2
    cap = prism_yz(tag + "_chaperon",
                   [(-(b - s), H - 12), (-o, H - 9), (-o, H - 4), (-top, H),
                    (top, H), (o, H - 4), (o, H - 9), ((b - s), H - 12)], 0, L)
    return [plinth, body, cap]

def _box_damage(tag, L, b, H, cuts, ends_free=True, max_rz=None):
    """Éclats sur les arêtes du sommet et fissures, pour une pièce allongée selon X."""
    lvl = _dmg()
    if not lvl:
        return
    n = max(1, round(L / (70 if lvl == 1 else 35)))
    for i in range(n):
        x = _rng.uniform(8, max(9, L - 8))
        side = _rng.choice((-1, 1))
        rz = _rng.uniform(3, 6) if lvl == 1 else _rng.uniform(6, 13)
        if max_rz:
            rz = min(rz, max_rz)
        rx = _rng.uniform(3, 6) if lvl == 1 else _rng.uniform(5, 10)
        cuts.append(chip(f"{tag}_eclat{i}", x, side * b, H, rx, b * 0.7, rz))
    if lvl == 2 and ends_free:
        x = _rng.choice((0, L))
        cuts.append(chip(f"{tag}_coin", x, _rng.choice((-1, 1)) * b, H, _rng.uniform(8, 13), b * 0.9,
                         min(_rng.uniform(14, 20), max_rz or 99)))
    return lvl

def tomb_wall(L_in, T_in=1.2, H_in=4.1, name=None):
    L, T, H = L_in * IN, T_in * IN, H_in * IN
    tag = name or f"mur_{L_in:.2f}"
    parts = _wall_core(L, T, H, tag)
    b, s, _ = _wall_dims(T)
    face = b - s
    pd = min(2.0, 0.22 * face)          # profondeur des panneaux
    gd = min(1.6, 0.18 * face)          # profondeur des fentes de gauss
    cuts = []
    n = max(1, round(L / (2 * IN)))
    pitch = L / n
    if pitch >= 20:
        for i in range(n):
            x0, x1 = i * pitch + 4, (i + 1) * pitch - 4
            xc = (x0 + x1) / 2
            for sign in (1, -1):
                cuts.append(_panel_cut(f"{tag}_p{i}{sign}", x0, x1, 14, H - 20, face, sign, pd))
                cuts.append(_slit_cut(f"{tag}_g{i}{sign}", xc, 4, 22, H - 32, face - pd, sign, gd))
                if not _plain():
                    for k in (-1, 0, 1):   # glyphe : trois encoches sous le chaperon
                        cuts.append(_slit_cut(f"{tag}_y{i}{sign}{k}", xc + k * 7, 2.4, H - 28, H - 24, face - pd, sign, min(1.0, gd)))
                if _engraved() and (x1 - x0) >= 30:
                    for j, dx in enumerate((-(x1 - x0) * 0.3, (x1 - x0) * 0.3)):
                        cuts += _glyphs(f"{tag}_sig{i}{sign}{j}", xc + dx, H - 34, 24, face - pd, sign, min(0.7, gd * 0.6))
    _magnets(tag, L, H, cuts)
    if _dmg():
        _box_damage(tag, L, b, H, cuts, ends_free=True, max_rz=18 if MAGNETS.get(_P.get("aimants")) else None)
        for i in range(max(1, round(L / 90)) * _dmg()):
            sign = _rng.choice((-1, 1))
            cuts += crack(f"{tag}_fissure{i}", _rng.uniform(10, L - 10), H - 14, _rng.uniform(15, 35) * _dmg(),
                          face - pd, sign, w=0.9 if _dmg() == 1 else 1.3, d=0.6 if _dmg() == 1 else 1.0)
    ob = build(tag, parts, cuts)
    _center(ob, L, 0)
    return ob

def door_tomb(W_in=3.0, T_in=1.2, H_in=4.1, name=None, jamb_in=None, buttress=False):
    """Cadre d'une porte nécron (seuil scellé, porte blindée, portail). Montants de
    min(0,6″, 18 % de la largeur), comme dans l'Atelier : passage libre de 1,92″
    pour 3″ (un socle de 40 mm passe). buttress : contreforts pour un portail autoportant."""
    W, T, H = W_in * IN, T_in * IN, H_in * IN
    tag = name or f"seuil_{W_in:.2f}"
    p = (jamb_in if jamb_in is not None else min(0.6, W_in * 0.18)) * IN
    Wo = W - 2 * p
    b, s, _ = _wall_dims(T)
    if buttress:   # portail : fronton de CREST mm au-dessus du cadre, hauteur totale inchangée
        H -= CREST
    parts = _wall_core(W, T, H, tag)
    if buttress:
        o = b - s / 2
        parts.append(prism_xz(tag + "_fronton", [(2, H - 4), (W - 2, H - 4), (W / 2 + 7, H + CREST), (W / 2 - 7, H + CREST)], -o, o))
        for k, (xa, xb) in enumerate(((0, p - 1), (W - p + 1, W))):
            for sign in (1, -1):
                parts.append(hull(f"{tag}_contrefort{k}{sign}",
                                  [(x, sign * y, z) for x in (xa, xb) for (y, z) in ((b - 1, 0), (b + 14, 0), (b + 14, 5), (b - 1, 46))]))
    zs = H - 40
    top = 20.0
    zt = zs + (Wo - top) / 2
    cuts = [prism_xz(tag + "_ouverture",
                     [(p, -1), (W - p, -1), (W - p, zs), (W / 2 + top / 2, zt), (W / 2 - top / 2, zt), (p, zs)],
                     -T - 30, T + 30)]
    cuts.append(box(tag + "_rainure", p - 3, W - p + 3, -SLOT_W / 2, SLOT_W / 2, -1, H + (CREST if buttress else 0) + 1))
    if buttress:   # diamant de gauss en creux sur les deux faces du fronton
        o, r, zc = b - s / 2, 5.0, H + CREST * 0.42
        for sign in (1, -1):
            cuts.append(hull(f"{tag}_g_fronton{sign}", [(W / 2 + dx, sign * y, zc + dz) for (dx, dz) in ((r, 0), (-r, 0), (0, r), (0, -r))
                                                        for y in (o + 1, o - 1.4)]))
    face = b - s
    gd = min(1.6, 0.18 * face)
    for sign in (1, -1):
        for k, xc in enumerate((p / 2, W - p / 2)):
            cuts.append(_slit_cut(f"{tag}_g{k}{sign}", xc, 3 if p > 10 else 2, 14, H - 22, face, sign, gd))
        if _engraved():   # frise de glyphes sur le linteau
            cuts += [c for c in _glyphs(f"{tag}_lin{sign}", W / 2, H - 13, H - 24, face, sign, 0.6, 0.8)]
    _magnets(tag, W, H, cuts)
    lvl = _dmg()
    if lvl:
        for i in range(lvl * 2):
            x = _rng.choice((_rng.uniform(2, p), _rng.uniform(W - p, W - 2)))
            cuts.append(chip(f"{tag}_eclat{i}", x, _rng.choice((-1, 1)) * b, H, _rng.uniform(2.5, 4) * lvl, b * 0.5, 2.5 * lvl))
        for i in range(lvl):
            sign = _rng.choice((-1, 1))
            cuts += crack(f"{tag}_fissure{i}", _rng.choice((p / 2, W - p / 2)) + _rng.uniform(-2, 2), H - 16,
                          18 * lvl, face, sign, w=0.8, d=0.6)
    ob = build(tag, parts, cuts)
    _center(ob, W, 0)
    return ob, Wo

def door_slab(Wo, H_in=4.1, name="porte"):
    """Battant de la porte, imprimé à plat (Z = épaisseur). Glisse dans la
    rainure par le haut : posé = fermé, retiré = ouvert."""
    H = H_in * IN
    w, h, t = Wo + 2 * (3 - 0.2), H - 2, SLAB_T - 0.2
    parts = [box(name + "_plaque", -w / 2, w / 2, 0, h, 0, t),
             box(name + "_prise", -8, 8, h - 1, h + 7, 0, t)]
    # diamant en relief + ligne de gauss, côté face visible (vers le haut à l'impression)
    cy, r = h * 0.48, min(w, h) * 0.26
    ring_out = hull(name + "_lum1", [(0, cy + r, t - .5), (r, cy, t - .5), (0, cy - r, t - .5), (-r, cy, t - .5),
                                   (0, cy + r, t + 1.2), (r, cy, t + 1.2), (0, cy - r, t + 1.2), (-r, cy, t + 1.2)])
    parts.append(ring_out)
    ri = r - 3
    cuts = [hull(name + "_d2", [(0, cy + ri, t), (ri, cy, t), (0, cy - ri, t), (-ri, cy, t),
                                (0, cy + ri, t + 2), (ri, cy, t + 2), (0, cy - ri, t + 2), (-ri, cy, t + 2)])]
    parts.append(box(name + "_lum2", -w / 2 + 5, w / 2 - 5, cy - 1.2, cy + 1.2, t - .5, t + 1.0))
    if not _plain():   # nervures verticales de blindage
        for dx in (-w * 0.3, w * 0.3):
            parts.append(box(name + f"_nervure{dx:.0f}", dx - 1.2, dx + 1.2, 6, cy - r - 2, t - .5, t + 0.8))
            parts.append(box(name + f"_nervureb{dx:.0f}", dx - 1.2, dx + 1.2, cy + r + 2, h - 6, t - .5, t + 0.8))
    ob = build(name, parts, cuts)
    return ob

def niche(W_in=3.0, D_in=1.6, H_in=4.0, name=None):
    """Niche de stase : fond côté +Y (haut du plan), ouverture côté -Y."""
    W, D, H = W_in * IN, D_in * IN, H_in * IN
    tag = name or "niche"
    t = 0.32 * IN
    b = D / 2
    parts = [box(tag + "_bloc", 0, W, -b, b, 0, H - 10),
             prism_xz(tag + "_chaperon", [(0, H - 12), (0, H - 4), (6, H), (W - 6, H), (W, H - 4), (W, H - 12)], -b, b)]
    zs, top = H - 36, 14.0
    zt = zs + (W - 2 * t - top) / 2
    cuts = [prism_xz(tag + "_alcove",
                     [(t, 8), (W - t, 8), (W - t, zs), (W / 2 + top / 2, zt), (W / 2 - top / 2, zt), (t, zs)],
                     -b - 1, b - t)]
    back = b - t
    pw, pz0, pz1 = 30.0, 14.0, 14.0 + 58.0
    pod = [(W / 2 - pw / 2, pz0), (W / 2 + pw / 2, pz0), (W / 2 + pw / 2, pz1 - 10), (W / 2 + 5, pz1), (W / 2 - 5, pz1), (W / 2 - pw / 2, pz1 - 10)]
    ob = build(tag, parts, cuts)          # bloc creusé d'abord, sinon l'alcôve efface la capsule
    pod_ob = prism_xz(tag + "_capsule", pod, back - 7, back + 0.5)
    slit = prism_xz(tag + "_fente", [(W / 2 - 1.6, pz0 + 6), (W / 2 + 1.6, pz0 + 6), (W / 2 + 1.6, pz1 - 14), (W / 2, pz1 - 12.4), (W / 2 - 1.6, pz1 - 14)], back - 8, back - 5.5)
    ob = build(tag, [ob, pod_ob], [slit])
    _center(ob, W, 0)
    return ob

def pillar(W_in=2.4, H_in=5.0, name="pilier"):
    """Pilier carré : socle, fût, chapiteau en pointe ; une fente de gauss par face."""
    W, H = W_in * IN, H_in * IN
    b = W / 2
    parts = [box(name + "_socle", -b, b, -b, b, 0, 8),
             box(name + "_fut", -(b - 4), b - 4, -(b - 4), b - 4, 6, H - 14),
             hull(name + "_chap", [(sx * (b - 4), sy * (b - 4), H - 16) for sx in (-1, 1) for sy in (-1, 1)] +
                  [(sx * (b - 1.5), sy * (b - 1.5), H - 12) for sx in (-1, 1) for sy in (-1, 1)] +
                  [(sx * (b - 10), sy * (b - 10), H) for sx in (-1, 1) for sy in (-1, 1)])]
    face, cuts = b - 4, []
    sw = 5 if W >= 50 else 4
    for sign in (1, -1):
        for axis in ("y", "x"):
            fc = [_slit_cut(f"{name}_g{axis}{sign}", 0, sw, 18, H - 30, face, sign, 1.8)]
            if _engraved():
                for j, dx in enumerate((-(face * 0.55), face * 0.55)):
                    fc += _glyphs(f"{name}_sig{axis}{sign}{j}", dx, H - 22, 20, face, sign, 0.7, 0.8 if W < 50 else 1.0)
                # bandeau gravé au-dessus du socle
                fc.append(_panel_cut(f"{name}_band{axis}{sign}", -(face - 3), face - 3, 12, 13.4, face, sign, 0.7))
            if axis == "x":
                for c in fc:
                    _swap_xy(c)
            cuts += fc
    lvl = _dmg()
    if lvl:
        corners = [(sx, sy) for sx in (-1, 1) for sy in (-1, 1)]
        _rng.shuffle(corners)
        for i, (sx, sy) in enumerate(corners[:lvl + 1]):
            cuts.append(chip(f"{name}_eclat{i}", sx * (b - 3), sy * (b - 3), H - 11,
                             _rng.uniform(3, 5) * lvl, _rng.uniform(3, 5) * lvl, _rng.uniform(4, 7) * lvl))
        for i in range(lvl):
            sign, axis = _rng.choice((-1, 1)), _rng.choice(("x", "y"))
            cuts += crack(f"{name}_fissure{i}", _rng.uniform(-face * 0.6, face * 0.6), H - 18, 25 * lvl, face, sign,
                          w=0.9, d=0.7, axis=axis, zmin=14)
    return build(name, parts, cuts)

def sarco(W_in=3.0, D_in=1.4, H_in=1.5, name="sarcophage"):
    """Sarcophage bas aux extrémités chanfreinées, ligne de gauss sur le couvercle."""
    W, D, H = W_in * IN, D_in * IN, H_in * IN
    b = D / 2
    body = hull(name + "_corps", [(x, y, z) for x in (0, W) for y in (-b, b) for z in (0,)] +
                [(x, y, H - 6) for x in (4, W - 4) for y in (-b, b)] +
                [(x, y, H) for x in (10, W - 10) for y in (-(b - 5), b - 5)])
    cuts = [box(name + "_g", 16, W - 16, -1.6, 1.6, H - 1.8, H + 2)]
    for k in range(5):
        x = 18 + k * (W - 36) / 4
        cuts.append(box(f"{name}_entaille{k}", x - 0.8, x + 0.8, -(b + 1), -(b - 2), 8, H - 10))
    if _engraved():
        # frise de glyphes couchée le long de la face arrière, entre deux filets
        cuts.append(_panel_cut(name + "_filet_h", 10, W - 10, H - 11, H - 10, b, 1, 0.6))
        cuts.append(_panel_cut(name + "_filet_b", 10, W - 10, 7, 8, b, 1, 0.6))
        x = 14
        while x < W - 16:
            g = _rng.choice(("barre", "point", "trait", "paire"))
            if g == "barre":
                cuts.append(_panel_cut(f"{name}_sig{x:.0f}", x, x + 6, 16, 17.2, b, 1, 0.6)); x += 9
            elif g == "point":
                cuts.append(_panel_cut(f"{name}_sig{x:.0f}", x, x + 2, 15.6, 17.6, b, 1, 0.6)); x += 5
            elif g == "paire":
                cuts.append(_panel_cut(f"{name}_sig{x:.0f}a", x, x + 2, 12, 14, b, 1, 0.6))
                cuts.append(_panel_cut(f"{name}_sig{x:.0f}b", x, x + 2, 19, 21, b, 1, 0.6)); x += 5
            else:
                cuts.append(_panel_cut(f"{name}_sig{x:.0f}", x, x + 1.2, 11, 22, b, 1, 0.6)); x += 4
        # diamant gravé sur le couvercle, aux deux bouts de la ligne de gauss
        for xc in (12, W - 12):
            cuts.append(hull(f"{name}_dia{xc:.0f}", [(xc, 3, H - 0.8), (xc + 3, 0, H - 0.8), (xc, -3, H - 0.8), (xc - 3, 0, H - 0.8),
                                                    (xc, 3, H + 1), (xc + 3, 0, H + 1), (xc, -3, H + 1), (xc - 3, 0, H + 1)]))
    lvl = _dmg()
    if lvl:
        for i in range(lvl + 1):
            x = _rng.uniform(12, W - 12)
            side = _rng.choice((-1, 1))
            cuts.append(chip(f"{name}_eclat{i}", x, side * (b - 3), H - 3, _rng.uniform(3, 5) * lvl, 3.5, _rng.uniform(2.5, 4) * lvl))
        cuts += crack(name + "_fissure", _rng.uniform(20, W - 20), H - 8, 12 * lvl, -b, -1, w=0.8, d=0.6, zmin=4)
    ob = build(name, [body], cuts)
    _center(ob, W, 0)
    return ob

def pylon_height(W_in):
    """Hauteur d'un pylône selon son empreinte : 8″ pour 5″ (catalogue), au moins
    4″ (Tir plongeant possible depuis 3″) et pas plus de 2,2 fois sa largeur."""
    return min(8.0, max(4.0, 2.2 * W_in))

def pylon(W_in=5.0, H_in=8.0, name="pylone"):
    """Pylône octogonal : socle large, fût, flèche ; fentes sur quatre faces."""
    W, H = W_in * IN, H_in * IN
    def oct_pts(r, z):
        return [(r * math.cos(math.radians(22.5 + 45 * k)), r * math.sin(math.radians(22.5 + 45 * k)), z) for k in range(8)]
    inset = min(18.0, W * 0.14)
    sh = min(14.0, max(8.0, W * 0.11))
    c22 = math.cos(math.radians(22.5))
    R0, R1 = W / 2 / c22, (W / 2 - inset) / c22
    zf = H * 0.72
    parts = [hull(name + "_socle", oct_pts(R0, 0) + oct_pts(R0 - 4, sh)),
             hull(name + "_fut", oct_pts(R1, sh - 2) + oct_pts(R1, zf)),
             hull(name + "_fleche", oct_pts(R1, zf - 6) + oct_pts(R1 + 3, zf - 3) + oct_pts(R1 + 3, zf + 4) + oct_pts(min(8, R1 * 0.45), H))]
    face, cuts = (W / 2 - inset), []
    sw = max(2.5, min(7.0, W * 0.055))
    for sign in (1, -1):
        for axis in ("y", "x"):
            fc = [_slit_cut(f"{name}_g{axis}{sign}", 0, sw, sh + 12, zf - 14, face, sign, min(2.2, face * 0.1))]
            if _engraved():
                for j, dx in enumerate((-face * 0.24, face * 0.24)):
                    fc += _glyphs(f"{name}_sig{axis}{sign}{j}", dx, zf - 12, sh + 10, face, sign, 0.7, 0.6 if W < 80 else 1.0)
            if axis == "x":
                for c in fc:
                    _swap_xy(c)
            cuts += fc
    lvl = _dmg()
    if lvl:
        for i in range(lvl + 1):
            a = _rng.uniform(0, 2 * math.pi)
            r = (R0 + R1) / 2 + 2
            cuts.append(chip(f"{name}_eclat{i}", r * math.cos(a), r * math.sin(a), sh, _rng.uniform(3, 5) * lvl, _rng.uniform(3, 5) * lvl, _rng.uniform(2, 4) * lvl))
        a = _rng.uniform(0, 2 * math.pi)
        cuts.append(chip(name + "_eclat_fleche", (R1 + 2) * math.cos(a), (R1 + 2) * math.sin(a), zf + 4, 3 * lvl, 3 * lvl, 4 * lvl))
        for i in range(lvl):
            cuts += crack(f"{name}_fissure{i}", _rng.uniform(-face * 0.5, face * 0.5), zf - 10, 20 * lvl, face,
                          _rng.choice((-1, 1)), w=0.8, d=0.6, axis=_rng.choice(("x", "y")), zmin=sh + 4)
    return build(name, parts, cuts)

def pyramid_height(w_in, d_in):
    """Hauteur d'une pyramide : 6″ pour 10″ (catalogue), proportionnelle à son
    empreinte, au moins 3,2″ pour que son sommet donne le Tir plongeant."""
    return max(3.2, 0.6 * min(w_in, d_in))

def pyramid(w_in=10.0, d_in=10.0, H_in=None, tiers=0, name="pyramide"):
    """Pyramide nécron tronquée (sommet plat où poser des figurines), lisse
    (tiers = 0) ou à gradins ; une rainure de gauss au milieu de chaque face."""
    H_in = H_in or pyramid_height(w_in, d_in)
    A, B, H = w_in * IN / 2, d_in * IN / 2, H_in * IN
    sk = 4.0                     # plinthe verticale
    e = min(A, B) * 0.68         # retrait du sommet (même pente sur les quatre faces)
    a, bb = A - e, B - e
    parts = []
    if tiers and tiers > 0:
        n = int(tiers)
        for i in range(n):
            ins = e * i / n
            parts.append(box(f"{name}_marche{i}", -(A - ins), A - ins, -(B - ins), B - ins, 0 if i == 0 else sk + (H - sk) * i / n - 0.01, sk + (H - sk) * (i + 1) / n))
    else:
        parts.append(hull(name + "_corps", [(sx * A, sy * B, 0) for sx in (-1, 1) for sy in (-1, 1)] +
                          [(sx * A, sy * B, sk) for sx in (-1, 1) for sy in (-1, 1)] +
                          [(sx * a, sy * bb, H) for sx in (-1, 1) for sy in (-1, 1)]))

    def zs(x, y):
        """Altitude de la surface au-dessus du point (x, y)."""
        m = min(A - abs(x), B - abs(y))
        if m < 0:
            return 0.0
        if tiers and tiers > 0:
            n = int(tiers)
            i = min(n - 1, int(m / (e / n))) if m < e else n - 1
            return sk + (H - sk) * (i + 1) / n
        return sk + (H - sk) * min(1.0, m / e)

    cuts = []
    def groove(tag, pts_xy, w, d):
        """Rainure qui suit la surface, ouverte vers le haut (sans surplomb)."""
        for k in range(len(pts_xy) - 1):
            (x0, y0), (x1, y1) = pts_xy[k], pts_xy[k + 1]
            L = math.hypot(x1 - x0, y1 - y0) or 1
            nx, ny = -(y1 - y0) / L * w / 2, (x1 - x0) / L * w / 2
            pts = [(x + sx * nx, y + sx * ny, zs(x, y) - d) for (x, y) in ((x0, y0), (x1, y1)) for sx in (-1, 1)]
            cuts.append(hull(f"{tag}_{k}", _up(pts)))
    # rainures de gauss au milieu des faces, du pied jusqu'au sommet
    for k, (ux, uy, half) in enumerate(((0, -1, B), (0, 1, B), (-1, 0, A), (1, 0, A))):
        p0 = (ux * (half - 6), uy * (half - 6))
        p1 = (ux * (half - e + 1), uy * (half - e + 1))
        groove(f"{name}_g{k}", [p0, p1], 3.0, 1.6)
    # arêtes soulignées d'un filet (sauf style sobre)
    if not _plain():
        for k, (sx, sy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
            groove(f"{name}_arete{k}", [(sx * (A - 5), sy * (B - 5)), (sx * (a + 1), sy * (bb + 1))], 1.2, 0.8)
    lvl = _dmg()
    if lvl:
        def pt_ridge(t, sx, sy):
            return (sx * (A - (A - a) * t), sy * (B - (B - bb) * t))
        # coins du sommet mordus
        tops = [(sx, sy) for sx in (-1, 1) for sy in (-1, 1)]
        _rng.shuffle(tops)
        for i, (sx, sy) in enumerate(tops[:lvl]):
            r = _rng.uniform(8, 12) * lvl
            cuts.append(chip(f"{name}_sommet{i}", sx * a, sy * bb, H, r, r, _rng.uniform(6, 10) * lvl))
        # éclats le long des arêtes
        for i in range(2 + 2 * lvl):
            sx, sy = _rng.choice((-1, 1)), _rng.choice((-1, 1))
            x, y = pt_ridge(_rng.uniform(0.2, 0.85), sx, sy)
            r = _rng.uniform(4, 7) * lvl
            cuts.append(chip(f"{name}_arete_eclat{i}", x, y, zs(x * 0.98, y * 0.98), r, r, _rng.uniform(3, 6) * lvl))
        # coins du pied
        for i in range(lvl):
            sx, sy = _rng.choice((-1, 1)), _rng.choice((-1, 1))
            cuts.append(chip(f"{name}_pied{i}", sx * A, sy * B, sk + 4, 9, 9, _rng.uniform(4, 7)))
        if lvl == 2:   # brèche dans une face
            ux, uy = _rng.choice(((0, -1), (0, 1), (-1, 0), (1, 0)))
            off = _rng.uniform(-0.35, 0.35)
            m = _rng.uniform(0.3, 0.55) * e
            x = ux * (A - m) + (0 if ux else off * a * 2)
            y = uy * (B - m) + (0 if uy else off * bb * 2)
            cuts.append(chip(name + "_breche", x, y, zs(x, y), 14, 14, 12))
        # fissures qui descendent les faces
        for i in range(2 * lvl + 1):
            ux, uy = _rng.choice(((0, -1), (0, 1), (-1, 0), (1, 0)))
            m = _rng.uniform(e * 0.6, e * 0.95)
            t = _rng.uniform(-0.6, 0.6)
            x = ux * (A - m) + (0 if ux else t * a)
            y = uy * (B - m) + (0 if uy else t * bb)
            pts = [(x, y)]
            for _ in range(_rng.randint(3, 5)):
                step = _rng.uniform(5, 9)
                lat = _rng.uniform(-4, 4)
                x = x + (ux * step if ux else lat)
                y = y + (uy * step if uy else lat)
                if abs(x) > A - 3 or abs(y) > B - 3:
                    break
                pts.append((x, y))
            if len(pts) > 1:
                groove(f"{name}_fissure{i}", pts, 0.9 + 0.4 * lvl, 0.6 + 0.4 * lvl)
    return build(name, parts, cuts)

def _center(ob, L, _):
    me = ob.data
    for v in me.vertices:
        v.co.x -= L / 2

# ---------------------------------------------------------------- rendu
def _material(name, rgb, rough=0.65, emit=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Roughness"].default_value = rough
    return m

def render(path, objs, table=None, cam=None, res=(1400, 900), samples=48, zoom=1.0):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.film_transparent = False
    world = bpy.data.worlds.new("w") if not sc.world else sc.world
    sc.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.025, 0.03, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
    bpy.context.view_layer.update()
    mins = Vector((1e9, 1e9, 1e9)); maxs = Vector((-1e9, -1e9, -1e9))
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            mins = Vector(map(min, mins, w)); maxs = Vector(map(max, maxs, w))
    if table:
        W, D = table
        g = box("table", 0, W, -D, 0, -3, 0)
    else:
        pad = 60
        g = box("sol", mins.x - pad, maxs.x + pad, mins.y - pad, maxs.y + pad, -3, 0)
    g.data.materials.clear()
    g.data.materials.append(_material("sol", (0.05, 0.055, 0.05), 0.9))
    ctr = (mins + maxs) / 2
    size = max(maxs.x - mins.x, maxs.y - mins.y, (maxs.z - mins.z) * 1.6, 120)
    cam_d = cam or (0, -1.15, 0.95)
    cd = bpy.data.cameras.new("cam"); cd.lens = 40; cd.clip_start = 1; cd.clip_end = 100000  # unités = mm
    co = bpy.data.objects.new("cam", cd); sc.collection.objects.link(co)
    co.location = ctr + Vector(cam_d) * size * zoom
    co.rotation_euler = (ctr - co.location).to_track_quat("-Z", "Y").to_euler()
    sc.camera = co
    lights = []
    for i, (loc, energy) in enumerate([((-1, -1, 2), 4.0), ((1.2, 0.4, 1.4), 1.8)]):
        ld = bpy.data.lights.new(f"sun{i}", "SUN"); ld.energy = energy; ld.angle = 0.2
        lo = bpy.data.objects.new(f"sun{i}", ld); sc.collection.objects.link(lo)
        lo.rotation_euler = (-Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        lights.append(lo)
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    for o in [g, co] + lights:   # la scène peut resservir pour un autre rendu
        bpy.data.objects.remove(o, do_unlink=True)

def render_lineup(path, obs, samples=24):
    """Rendu des pièces d'un design, rangées en lignes successives (de l'avant
    vers le fond) pour rester lisibles. obs : objets ou (objet, 'plat')."""
    shown, items = [], []
    for item in obs:
        o, flat = (item if isinstance(item, tuple) else (item, None))
        c = o.copy(); c.data = o.data
        bpy.context.scene.collection.objects.link(c)
        c.hide_render = False
        if flat:   # battant : montré debout
            c.rotation_euler = (math.radians(90), 0, 0)
        bpy.context.view_layer.update()
        bb = [c.matrix_world @ Vector(v) for v in c.bound_box]
        xs, ys = [p.x for p in bb], [p.y for p in bb]
        items.append((c, min(xs), max(xs) - min(xs), min(ys), max(ys) - min(ys)))
        shown.append(c)
    total = sum(w for _, _, w, _, _ in items)
    row_w = max(max(w for _, _, w, _, _ in items), min(total, max(360.0, math.sqrt(total * 260))))
    x = y = 0.0
    row_d, row_h = 0.0, 0.0
    for c, x0, w, y0, d in items:
        if x > 0 and x + w > row_w:   # ligne suivante, assez loin pour ne pas masquer la précédente
            x, y, row_d, row_h = 0.0, y + row_d + max(35.0, row_h * 0.9), 0.0, 0.0
        row_h = max(row_h, c.dimensions.y if c.rotation_euler.x else c.dimensions.z)
        c.location.x += x - x0
        c.location.y += y - y0
        x += w + 18
        row_d = max(row_d, d)
    hidden = [o for o in bpy.context.scene.objects if o not in shown and o.type == "MESH"]
    for o in hidden:
        o.hide_render = True
    render(path, shown, cam=(0.12, -1.0, 0.6), res=(1200, 700), samples=samples, zoom=2.0)
    for o in shown:
        bpy.data.objects.remove(o, do_unlink=True)
    for o in hidden:
        o.hide_render = False

# ---------------------------------------------------------------- kit et carte
def _bed_split(L_in, bed_mm):
    max_in = (bed_mm - 4) / IN
    n = max(1, math.ceil(L_in / max_in - 1e-6))
    return n, L_in / n

def make_kit(out, bed=220, do_render=False):
    os.makedirs(out, exist_ok=True)
    _scene_reset()
    set_params()
    T, H = CATALOG["tomb_wall"][1], CATALOG["tomb_wall"][2]
    made, x = [], 0
    for L in (8, 4, 2):
        ob = tomb_wall(L, T, H, f"mur-tombe_{L}in")
        write_stl(ob, os.path.join(out, f"mur-tombe_{L}in.stl"))
        made.append((ob, L * IN))
    fr, Wo = door_tomb(3.0, T, H, "seuil-scelle_3in")
    write_stl(fr, os.path.join(out, "seuil-scelle_3in_cadre.stl"))
    made.append((fr, 3 * IN))
    sl = door_slab(Wo, H, "seuil-scelle_3in_porte")
    write_stl(sl, os.path.join(out, "seuil-scelle_3in_porte_a-plat.stl"))
    ni = niche(*CATALOG["niche"], name="niche-de-stase")
    write_stl(ni, os.path.join(out, "niche-de-stase.stl"))
    made.append((ni, 3 * IN))
    gw, gd, gh = CATALOG["gate"]
    ga, gWo = door_tomb(gw, T, gh, "portail-de-tombe_4in", jamb_in=min(0.8, gw * 0.2))
    write_stl(ga, os.path.join(out, "portail-de-tombe_4in_cadre.stl"))
    write_stl(door_slab(gWo, gh, "portail-de-tombe_4in_porte"), os.path.join(out, "portail-de-tombe_4in_porte_a-plat.stl"))
    made.append((ga, gw * IN))
    for fn, args, fname, w in ((pillar, (2.4, 5.0), "pilier", 2.4), (sarco, (3.0, 1.4, 1.5), "sarcophage", 3.0), (pylon, (5.0, 8.0), "pylone", 5.0)):
        ob = fn(*args, name=fname)
        write_stl(ob, os.path.join(out, fname + ".stl"))
        made.append((ob, w * IN))
    if do_render:
        for ob, w in made:
            ob.location.x = x + w / 2
            x += w + 25
        sl.location = (x + 40, 0, 0)
        sl.rotation_euler = (math.radians(90), 0, 0)
        sl.location.z = 0
        render(os.path.join(out, "apercu-kit.png"), [o for o, _ in made] + [sl], cam=(0.15, -1.0, 0.62))
    return out

def _fmt(v):
    return f"{v:.2f}".replace(".", ",")

DOORS = {"door_tomb", "door_hive", "gate"}

def make_from_layout(path, out, bed=220, do_render=False, designs=None):
    """designs : None (tous les types générables, réglages par défaut) ou
    {type: {"slug", "params", "seed", "h_in"}} (seuls ces types sont générés,
    chacun dans out/<slug>/)."""
    os.makedirs(out, exist_ok=True)
    data = json.load(open(path, encoding="utf-8"))
    lay = data.get("layout", data)
    W_in = float(data.get("width_in", 44)); D_in = float(data.get("depth_in", 30))
    feats = lay.get("features", [])
    _scene_reset()
    gen_types = set(designs) if designs is not None else set(NAMES)
    cache, placed, notes, counts = {}, [], [], {}   # counts[(type, fichier)] = quantité
    files = {}                                        # type -> [(fichier, objet, taille_in, à plat)]
    def dz(t):
        return designs.get(t, {}) if designs else {}
    def get(t, key, fn):
        if (t, key) not in cache:
            d = dz(t)
            set_params(d.get("params"), (d.get("seed") or t) + "|" + "|".join(str(k) for k in key))
            cache[(t, key)] = fn()
        return cache[(t, key)]
    def slug(t):
        return dz(t).get("slug") or NAMES.get(t, t)
    def add(t, fname, n=1):
        counts[(t, fname)] = counts.get((t, fname), 0) + n
    for f in feats:
        t, w, h = f["type"], float(f["w"]), float(f["h"])
        x, y, rot = float(f["x"]) * IN, -float(f["y"]) * IN, -math.radians(float(f.get("rot", 0)))
        if t not in gen_types or t not in NAMES:
            notes.append(t)
            hh = PLACEHOLDER_H.get(t, 2) * IN
            ph = box(f"a-sourcer_{t}", -w * IN / 2, w * IN / 2, -h * IN / 2, h * IN / 2, 0, max(hh, 4))
            ph.data.materials.clear(); ph.data.materials.append(mat("asourcer"))
            ph.location = (x, y, 0); ph.rotation_euler = (0, 0, rot)
            placed.append((None, ph, None))
            continue
        H_t = dz(t).get("h_in") or CATALOG[t][2]
        if t == "tomb_wall":
            n, Lp = _bed_split(w, bed)
            key = (round(Lp, 2), round(h, 2))
            fname = f"{slug(t)}_{_fmt(Lp)}in_ep{_fmt(h)}"
            src = get(t, key, lambda: (tomb_wall(Lp, h, H_t, fname), fname, None, Lp))
            add(t, fname, n)
            ux, uy = math.cos(rot), math.sin(rot)
            for i in range(n):
                off = (-w / 2 + Lp * (i + 0.5)) * IN
                placed.append((src[0], (x + ux * off, y + uy * off, 0), rot))
        elif t in DOORS:
            key = (round(w, 2), round(h, 2))
            fname = f"{slug(t)}_{_fmt(w)}in_ep{_fmt(h)}"
            def mk(t=t, w=w, h=h, fname=fname, H_t=H_t):
                if t == "gate":
                    fr, Wo = door_tomb(w, max(h, 0.5), H_t, fname + "_cadre", jamb_in=min(0.8, w * 0.2), buttress=True)
                    Hs = H_t - CREST / IN
                else:
                    fr, Wo = door_tomb(w, h, H_t, fname + "_cadre")
                    Hs = H_t
                return (fr, fname + "_cadre", door_slab(Wo, Hs, fname + "_porte_a-plat"), w)
            src = get(t, key, mk)
            add(t, src[1]); add(t, fname + "_porte_a-plat")
            placed.append((src[0], (x, y, 0), rot))
            if not f.get("open"):
                placed.append((src[2], (x, y, 0), ("slab", rot)))
        elif t == "niche":
            key = (round(w, 2), round(h, 2))
            fname = f"{slug(t)}_{_fmt(w)}x{_fmt(h)}in"
            src = get(t, key, lambda: (niche(w, h, H_t, fname), fname, None, w))
            add(t, fname)
            placed.append((src[0], (x, y, 0), rot))
        elif t in ("pillar", "sarco", "pylon", "pyramid"):
            key = (round(w, 2), round(h, 2), int(f.get("tiers", 0) or 0))
            fname = f"{slug(t)}_{_fmt(w)}x{_fmt(h)}in"
            if t == "pyramid" and key[2]:
                fname += f"_{key[2]}gradins"
            fn = {"pillar": lambda: pillar(min(w, h), H_t, fname),
                  "sarco": lambda: sarco(w, h, H_t, fname),
                  "pylon": lambda: pylon(min(w, h), dz(t).get("h_in") or pylon_height(min(w, h)), fname),
                  "pyramid": lambda: pyramid(w, h, dz(t).get("h_in"), key[2], fname)}[t]
            src = get(t, key, lambda: (fn(), fname, None, w))
            add(t, fname)
            placed.append((src[0], (x, y, 0), rot))
    # STL : une pièce par forme, rangées par design
    for (t, key), v in cache.items():
        d = os.path.join(out, slug(t)) if designs is not None else out
        os.makedirs(d, exist_ok=True)
        write_stl(v[0], os.path.join(d, v[1] + ".stl"))
        files.setdefault(t, []).append((v[1], v[0], v[3], False))
        if v[2] is not None:
            write_stl(v[2], os.path.join(d, v[1].replace("_cadre", "") + "_porte_a-plat.stl"))
            files[t].append((v[1].replace("_cadre", "") + "_porte_a-plat", v[2], v[3], True))
    # liste d'impression
    lines = [f"# Liste d'impression — {data.get('name', 'carte')}", "",
             f"Table de {W_in:g}″ × {D_in:g}″. Pièces générées aux cotes de la carte (mm, FDM sans supports, debout ; battants à plat).", "",
             "| Pièce | Fichier | Quantité |", "|---|---|---|"]
    for (t, k) in sorted(counts, key=lambda c: (slug(c[0]), c[1])):
        lines.append(f"| {slug(t)} | {k}.stl | {counts[(t, k)]} |")
    if notes:
        miss = {}
        for t in notes:
            miss[t] = miss.get(t, 0) + 1
        lines += ["", "Non générés (voir les fichiers STL trouvés dans le panneau Besoins) :", ""]
        lines += [f"- {k} × {v}" for k, v in sorted(miss.items())]
    mags = {}
    for (t, k), c in counts.items():
        if k.endswith("porte_a-plat") or t not in ("tomb_wall", "door_tomb", "door_hive", "gate"):
            continue
        m = str(dz(t).get("params", {}).get("aimants", "6x3")) if designs else "6x3"
        if m in MAGNETS:
            mags[m] = mags.get(m, 0) + 4 * c
    if mags:
        lines += [""] + [f"Aimants {m.replace('x', ' × ')} mm : {n} (2 par extrémité de mur, de cadre de porte et de portail)." for m, n in sorted(mags.items())]
        if len(mags) > 1:
            lines.append("Les aimants de tailles différentes s'attirent : ils sont aux mêmes hauteurs sur toutes les pièces. Garder la même polarité partout (marquer une face).")
    open(os.path.join(out, "liste-impression.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    # rendus : un par design, puis la table
    if do_render and designs is not None:
        for t, fl in files.items():
            obs = [(o, "plat") if flat else o for (_, o, _, flat) in sorted(fl, key=lambda f: (f[3], -f[2]))]
            render_lineup(os.path.join(out, slug(t), "rendu.png"), obs)
    if do_render:
        objs = []
        for src, loc, rot in placed:
            if src is None:
                objs.append(loc); continue
            o = src.copy(); o.data = src.data
            bpy.context.scene.collection.objects.link(o)
            if isinstance(rot, tuple):  # battant : debout dans la rainure
                o.rotation_euler = (math.radians(90), 0, rot[1]); o.location = (loc[0], loc[1], 1)
                o.location.x -= math.sin(rot[1]) * (SLAB_T - 0.2) / 2   # battant centré dans la rainure
                o.location.y += math.cos(rot[1]) * (SLAB_T - 0.2) / 2
            else:
                o.rotation_euler = (0, 0, rot); o.location = loc
            o.hide_render = False
            objs.append(o)
        for v in cache.values():
            v[0].hide_render = True
            if v[2] is not None:
                v[2].hide_render = True
        render(os.path.join(out, "apercu-table.png"), objs, table=(W_in * IN, D_in * IN), cam=(0.0, -0.95, 0.85), res=(1400, 960), samples=32)
    return {f"{slug(t)}/{k}": c for (t, k), c in counts.items()}, notes

# ---------------------------------------------------------------- entrée
# Dans l'interface de Blender (fichier vide : le script efface la scène) :
#   mode "kit" ou "carte" ; layout = chemin du JSON exporté par l'Atelier.
PARAMS_GUI = {"mode": "kit", "layout": "", "designs": "", "out": os.path.expanduser("~/cytherea_stl"), "bed": 220, "render": False}

def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None
    if argv is None and bpy.app.background is False:
        P = PARAMS_GUI
        out = P["out"]
        if P["mode"] == "kit":
            make_kit(out, P["bed"], P["render"])
        else:
            ds = json.load(open(bpy.path.abspath(P["designs"]), encoding="utf-8")) if P.get("designs") else None
            make_from_layout(bpy.path.abspath(P["layout"]), out, P["bed"], P["render"], ds)
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", action="store_true")
    ap.add_argument("--layout")
    ap.add_argument("--designs")
    ap.add_argument("--out", default="cytherea_stl")
    ap.add_argument("--bed", type=float, default=220)
    ap.add_argument("--render", action="store_true")
    a = ap.parse_args(argv or [])
    if a.layout:
        ds = json.load(open(a.designs, encoding="utf-8")) if a.designs else None
        c, n = make_from_layout(a.layout, a.out, a.bed, a.render, ds)
        print("pièces :", c, "| non générés :", n)
    else:
        make_kit(a.out, a.bed, a.render)
        print("kit écrit dans", a.out)

if __name__ == "__main__":
    main()
