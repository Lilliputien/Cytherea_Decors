"""
CYTHEREA — Générateur de décors imprimables (Blender 4.2+ / 5.x)
================================================================

Fabrique des décors nécrons pour la tombe de Naogeth, aux cotes exactes
de l'Atelier des cartes (catalogue decor_types, pouces), en STL millimétrés,
imprimables en FDM sans supports (pièces debout, surplombs ≤ 45°).

Pièces :
  - mur de tombe   (tomb_wall)  longueur au choix, épaisseur 1,2″, hauteur 4,1″
  - seuil scellé   (door_tomb)  cadre + porte amovible qui glisse par le haut
  - niche de stase (niche)      alcôve avec capsule de stase
Chaque extrémité de mur et de seuil a deux logements d'aimants 6 × 3 mm.

Utilisation
-----------
1. Le kit de base (8″, 4″, 2″, seuil, niche) :
     blender --background --python cytherea_terrain.py -- --kit --out kit
2. Depuis une carte exportée de l'Atelier (bouton JSON) :
     blender --background --python cytherea_terrain.py -- --layout carte.json --out carte
   -> une pièce par longueur de mur réellement posée (murs trop longs pour le
      plateau coupés en parts égales), les seuils, les niches, une liste
      d'impression et un rendu 3D de la table.
3. Dans l'interface de Blender : onglet Scripting, ouvrir ce fichier,
   régler PARAMS_GUI en bas du fichier, puis Exécuter.
Options : --bed 220 (taille du plateau en mm), --render (rendus Cycles).

Repères : 1 pouce = 25,4 mm. Carte de l'Atelier : x vers la droite,
y vers le bas ; dans Blender, Y = -y (le haut du plan est au fond).
"""
import bpy, bmesh, math, os, sys, json, argparse, struct
from mathutils import Vector

IN = 25.4
CATALOG = {  # cotes du catalogue Supabase (pouces) : largeur, profondeur, hauteur
    "tomb_wall": (8.0, 1.2, 4.1),
    "door_tomb": (3.0, 1.0, 4.1),
    "niche": (3.0, 1.6, 4.0),
    "gate": (4.0, 1.0, 4.0),
    "pillar": (2.4, 2.4, 5.0),
    "sarco": (3.0, 1.4, 1.5),
    "pylon": (5.0, 5.0, 8.0),
}
# hauteurs du catalogue pour les décors non générés (blocs « à sourcer » du rendu)
PLACEHOLDER_H = {"ruin_l": 6, "ruin_box": 5, "wall": 3, "muraille": 6, "container": 3, "rubble": 1, "crater": 0.3,
                 "trench": 0.3, "sandbags": 1, "barricade": 1.5, "wire": 1, "door_hive": 3, "wall_door": 3,
                 "tomb_wall_door": 4.1, "door_bunker": 2}
MAGNET_D, MAGNET_DEPTH = 6.3, 3.3      # aimants 6 × 3 mm, avec jeu
SLOT_W, SLAB_T = 3.5, 3.0              # rainure de la porte et épaisseur du battant

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
    if kind not in _MATS:
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

# ---------------------------------------------------------------- profils communs
def _wall_core(L, T, H, tag):
    """Plinthe, corps et chaperon chanfreiné d'un mur, de x = 0 à L."""
    b = T / 2
    plinth = box(tag + "_plinthe", 0, L, -b, b, 0, 6)
    body = box(tag + "_corps", 0, L, -(b - 3), b - 3, 5, H - 10)
    cap = prism_yz(tag + "_chaperon",
                   [(-(b - 3), H - 12), (-(b - 1.5), H - 9), (-(b - 1.5), H - 4), (-(b - 7), H),
                    ((b - 7), H), ((b - 1.5), H - 4), ((b - 1.5), H - 9), ((b - 3), H - 12)], 0, L)
    return [plinth, body, cap]

def _panel_cut(tag, x0, x1, z0, z1, face, sign, d):
    """Panneau en creux sur une face (sign = +1 face avant, -1 face arrière),
    haut chanfreiné à 45° pour imprimer debout sans support."""
    out, ins = face + 1, face - d
    pts = []
    for x in (x0, x1):
        pts += [(x, sign * out, z0), (x, sign * ins, z0), (x, sign * out, z1 + 1), (x, sign * ins, z1 - d)]
    return hull(tag, pts)

def _slit_cut(tag, xc, w, z0, z1, face, sign, d):
    return _panel_cut(tag, xc - w / 2, xc + w / 2, z0, z1, face, sign, d)

def _magnets(tag, L, H, cuts, xs=(0, None)):
    for x_end in (0, L):
        x0, x1 = (-1, MAGNET_DEPTH) if x_end == 0 else (L - MAGNET_DEPTH, L + 1)
        for k, z in enumerate((24, H - 36)):
            cuts.append(teardrop_x(f"{tag}_aimant{x_end}_{k}", x0, x1, 0, z, MAGNET_D))

# ---------------------------------------------------------------- pièces
def tomb_wall(L_in, T_in=1.2, H_in=4.1, name=None):
    L, T, H = L_in * IN, T_in * IN, H_in * IN
    tag = name or f"mur_{L_in:.2f}"
    parts = _wall_core(L, T, H, tag)
    cuts, face = [], T / 2 - 3
    n = max(1, round(L / (2 * IN)))
    pitch = L / n
    if pitch >= 20:
        for i in range(n):
            x0, x1 = i * pitch + 4, (i + 1) * pitch - 4
            xc = (x0 + x1) / 2
            for sign in (1, -1):
                cuts.append(_panel_cut(f"{tag}_p{i}{sign}", x0, x1, 14, H - 20, face, sign, 2.0))
                cuts.append(_slit_cut(f"{tag}_g{i}{sign}", xc, 4, 22, H - 32, face - 2.0, sign, 1.6))
                # glyphe : trois encoches sous le chaperon
                for k in (-1, 0, 1):
                    cuts.append(_slit_cut(f"{tag}_y{i}{sign}{k}", xc + k * 7, 2.4, H - 28, H - 24, face - 2.0, sign, 1.0))
    _magnets(tag, L, H, cuts)
    ob = build(tag, parts, cuts)
    _center(ob, L, 0)
    return ob

def door_tomb(W_in=3.0, T_in=1.2, H_in=4.1, name=None, jamb_in=None):
    """Cadre du seuil scellé. Montants de min(0,6″, 18 % de la largeur), comme
    dans l'Atelier : passage libre de 1,92″ pour 3″ (un socle de 40 mm passe)."""
    W, T, H = W_in * IN, T_in * IN, H_in * IN
    tag = name or f"seuil_{W_in:.2f}"
    p = (jamb_in if jamb_in is not None else min(0.6, W_in * 0.18)) * IN
    Wo = W - 2 * p
    parts = _wall_core(W, T, H, tag)
    zs = H - 40
    top = 20.0
    zt = zs + (Wo - top) / 2
    cuts = [prism_xz(tag + "_ouverture",
                     [(p, -1), (W - p, -1), (W - p, zs), (W / 2 + top / 2, zt), (W / 2 - top / 2, zt), (p, zs)],
                     -T, T)]
    cuts.append(box(tag + "_rainure", p - 3, W - p + 3, -SLOT_W / 2, SLOT_W / 2, -1, H + 1))
    face = T / 2 - 3
    for sign in (1, -1):
        for k, xc in enumerate((p / 2, W - p / 2)):
            cuts.append(_slit_cut(f"{tag}_g{k}{sign}", xc, 3, 14, H - 22, face, sign, 1.6))
    _magnets(tag, W, H, cuts)
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
    # capsule de stase contre le fond, sommet en pointe (45°)
    back = b - t
    pw, pz0, pz1 = 30.0, 14.0, 14.0 + 58.0
    pod = [(W / 2 - pw / 2, pz0), (W / 2 + pw / 2, pz0), (W / 2 + pw / 2, pz1 - 10), (W / 2 + 5, pz1), (W / 2 - 5, pz1), (W / 2 - pw / 2, pz1 - 10)]
    ob = build(tag, parts, cuts)          # bloc creusé d'abord, sinon l'alcôve efface la capsule
    pod_ob = prism_xz(tag + "_capsule", pod, back - 7, back + 0.5)
    slit = prism_xz(tag + "_fente", [(W / 2 - 1.6, pz0 + 6), (W / 2 + 1.6, pz0 + 6), (W / 2 + 1.6, pz1 - 14), (W / 2, pz1 - 12.4), (W / 2 - 1.6, pz1 - 14)], back - 8, back - 5.5)
    ob = build(tag, [ob, pod_ob], [slit])
    _center(ob, W, 0)
    return ob

def _swap(pts):
    return [(y, x, z) for (x, y, z) in pts]

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
    for sign in (1, -1):
        c = _slit_cut(f"{name}_gy{sign}", 0, 5, 18, H - 30, face, sign, 1.8)
        cuts.append(c)
        c2 = _slit_cut(f"{name}_gx{sign}", 0, 5, 18, H - 30, face, sign, 1.8)
        for v in c2.data.vertices:
            v.co.x, v.co.y = v.co.y, v.co.x
        cuts.append(c2)
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
    ob = build(name, [body], cuts)
    _center(ob, W, 0)
    return ob

def pylon(W_in=5.0, H_in=8.0, name="pylone"):
    """Pylône octogonal : socle large, fût, flèche ; fentes sur quatre faces."""
    W, H = W_in * IN, H_in * IN
    def oct_pts(r, z):
        return [(r * math.cos(math.radians(22.5 + 45 * k)), r * math.sin(math.radians(22.5 + 45 * k)), z) for k in range(8)]
    R0, R1 = W / 2 / math.cos(math.radians(22.5)), (W / 2 - 18) / math.cos(math.radians(22.5))
    parts = [hull(name + "_socle", oct_pts(R0, 0) + oct_pts(R0 - 4, 14)),
             hull(name + "_fut", oct_pts(R1, 12) + oct_pts(R1, H * 0.72)),
             hull(name + "_fleche", oct_pts(R1, H * 0.72 - 6) + oct_pts(R1 + 3, H * 0.72 - 3) + oct_pts(R1 + 3, H * 0.72 + 4) + oct_pts(8, H))]
    face, cuts = (W / 2 - 18), []
    for sign in (1, -1):
        cuts.append(_slit_cut(f"{name}_gy{sign}", 0, 7, 26, H * 0.72 - 14, face, sign, 2.2))
        c2 = _slit_cut(f"{name}_gx{sign}", 0, 7, 26, H * 0.72 - 14, face, sign, 2.2)
        for v in c2.data.vertices:
            v.co.x, v.co.y = v.co.y, v.co.x
        cuts.append(c2)
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

def render(path, objs, table=None, cam=None, res=(1400, 900), samples=48):
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
    g.data.materials.append(_material("sol", (0.05, 0.055, 0.05), 0.9))
    ctr = (mins + maxs) / 2
    size = max(maxs.x - mins.x, maxs.y - mins.y, 120)
    cam_d = cam or (0, -1.15, 0.95)
    cd = bpy.data.cameras.new("cam"); cd.lens = 40; cd.clip_start = 1; cd.clip_end = 100000  # unités = mm
    co = bpy.data.objects.new("cam", cd); sc.collection.objects.link(co)
    co.location = ctr + Vector(cam_d) * size * 1.0
    co.rotation_euler = (ctr - co.location).to_track_quat("-Z", "Y").to_euler()
    sc.camera = co
    for i, (loc, energy) in enumerate([((-1, -1, 2), 4.0), ((1.2, 0.4, 1.4), 1.8)]):
        ld = bpy.data.lights.new(f"sun{i}", "SUN"); ld.energy = energy; ld.angle = 0.2
        lo = bpy.data.objects.new(f"sun{i}", ld); sc.collection.objects.link(lo)
        lo.rotation_euler = (-Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)

# ---------------------------------------------------------------- kit et carte
def _bed_split(L_in, bed_mm):
    max_in = (bed_mm - 4) / IN
    n = max(1, math.ceil(L_in / max_in - 1e-6))
    return n, L_in / n

def make_kit(out, bed=220, do_render=False):
    os.makedirs(out, exist_ok=True)
    _scene_reset()
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

FEATURE_MAKERS = {"tomb_wall", "door_tomb", "niche"}

def make_from_layout(path, out, bed=220, do_render=False):
    os.makedirs(out, exist_ok=True)
    data = json.load(open(path, encoding="utf-8"))
    lay = data.get("layout", data)
    W_in = float(data.get("width_in", 44)); D_in = float(data.get("depth_in", 30))
    feats = lay.get("features", [])
    _scene_reset()
    cache, placed, notes, counts = {}, [], [], {}
    def get(kind, key, fn):
        if (kind, key) not in cache:
            cache[(kind, key)] = fn()
        return cache[(kind, key)]
    for f in feats:
        t, w, h = f["type"], float(f["w"]), float(f["h"])
        x, y, rot = float(f["x"]) * IN, -float(f["y"]) * IN, -math.radians(float(f.get("rot", 0)))
        if t == "tomb_wall":
            n, Lp = _bed_split(w, bed)
            key = (round(Lp, 2), round(h, 2))
            fname = f"mur-tombe_{Lp:.2f}in_ep{h:.2f}".replace(".", ",")
            src = get("wall", key, lambda: (tomb_wall(Lp, h, CATALOG["tomb_wall"][2], fname), fname))
            counts[src[1]] = counts.get(src[1], 0) + n
            ux, uy = math.cos(rot), math.sin(rot)
            for i in range(n):
                off = (-w / 2 + Lp * (i + 0.5)) * IN
                placed.append((src[0], (x + ux * off, y + uy * off, 0), rot))
        elif t == "door_tomb":
            key = (round(w, 2), round(h, 2))
            fname = f"seuil-scelle_{w:.2f}in_ep{h:.2f}".replace(".", ",")
            def mk():
                fr, Wo = door_tomb(w, h, CATALOG["door_tomb"][2], fname)
                sl = door_slab(Wo, CATALOG["door_tomb"][2], fname + "_porte")
                return (fr, fname, sl)
            src = get("door", key, mk)
            counts[src[1]] = counts.get(src[1], 0) + 1
            counts[src[1] + "_porte_a-plat"] = counts.get(src[1] + "_porte_a-plat", 0) + 1
            placed.append((src[0], (x, y, 0), rot))
            if not f.get("open"):
                placed.append((src[2], (x, y, 0), ("slab", rot)))
        elif t == "niche":
            key = (round(w, 2), round(h, 2))
            fname = f"niche-de-stase_{w:.2f}x{h:.2f}in".replace(".", ",")
            src = get("niche", key, lambda: (niche(w, h, CATALOG["niche"][2], fname), fname))
            counts[src[1]] = counts.get(src[1], 0) + 1
            placed.append((src[0], (x, y, 0), rot))
        elif t == "gate":
            key = (round(w, 2), round(h, 2))
            fname = f"portail-de-tombe_{w:.2f}in".replace(".", ",")
            def mkg():
                fr, Wo = door_tomb(w, max(h, 1.0), CATALOG["gate"][2], fname, jamb_in=min(0.8, w * 0.2))
                return (fr, fname, door_slab(Wo, CATALOG["gate"][2], fname + "_porte"))
            src = get("gate", key, mkg)
            counts[src[1]] = counts.get(src[1], 0) + 1
            counts[src[1] + "_porte_a-plat"] = counts.get(src[1] + "_porte_a-plat", 0) + 1
            placed.append((src[0], (x, y, 0), rot))
            if not f.get("open"):
                placed.append((src[2], (x, y, 0), ("slab", rot)))
        elif t in ("pillar", "sarco", "pylon"):
            key = (round(w, 2), round(h, 2))
            fname = {"pillar": "pilier", "sarco": "sarcophage", "pylon": "pylone"}[t] + f"_{w:.2f}x{h:.2f}in".replace(".", ",")
            fn = {"pillar": lambda: pillar(min(w, h), CATALOG["pillar"][2], fname),
                  "sarco": lambda: sarco(w, h, CATALOG["sarco"][2], fname),
                  "pylon": lambda: pylon(min(w, h), CATALOG["pylon"][2], fname)}[t]
            src = get(t, key, lambda: (fn(), fname))
            counts[src[1]] = counts.get(src[1], 0) + 1
            placed.append((src[0], (x, y, 0), rot))
        else:
            notes.append(t)
            hh = PLACEHOLDER_H.get(t, 2) * IN
            ph = box(f"a-sourcer_{t}", -w * IN / 2, w * IN / 2, -h * IN / 2, h * IN / 2, 0, max(hh, 4))
            ph.data.materials.clear(); ph.data.materials.append(mat("asourcer"))
            ph.location = (x, y, 0); ph.rotation_euler = (0, 0, rot)
            placed.append((None, ph, None))
    # STL : une pièce par forme
    for v in cache.values():
        write_stl(v[0], os.path.join(out, v[1] + ".stl"))
        if len(v) > 2:
            write_stl(v[2], os.path.join(out, v[1] + "_porte_a-plat.stl"))
    lines = [f"# Liste d'impression — {data.get('name', 'carte')}", "",
             f"Table de {W_in:g}″ × {D_in:g}″. Pièces générées aux cotes de la carte (mm, FDM sans supports, debout).", "",
             "| Fichier | Quantité |", "|---|---|"]
    for k in sorted(counts):
        lines.append(f"| {k}.stl | {counts[k]} |")
    if notes:
        miss = {}
        for t in notes:
            miss[t] = miss.get(t, 0) + 1
        lines += ["", "Non générés (voir les fichiers STL trouvés dans le panneau Besoins) :", ""]
        lines += [f"- {k} × {v}" for k, v in sorted(miss.items())]
    mag = sum(c for k, c in counts.items() if k.startswith(("mur-", "seuil-", "portail-")) and not k.endswith("porte_a-plat")) * 4
    lines += ["", f"Aimants 6 × 3 mm : {mag} (2 par extrémité de mur, de seuil et de portail)."]
    open(os.path.join(out, "liste-impression.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    if do_render:
        objs = []
        for src, loc, rot in placed:
            if src is None:
                objs.append(loc); continue
            o = src.copy(); o.data = src.data
            bpy.context.scene.collection.objects.link(o)
            if isinstance(rot, tuple):  # battant : debout dans la rainure
                o.rotation_euler = (math.radians(90), 0, rot[1]); o.location = (loc[0], loc[1], 1)
            else:
                o.rotation_euler = (0, 0, rot); o.location = loc
            objs.append(o)
        for v in cache.values():
            v[0].hide_render = True
            if len(v) > 2:
                v[2].hide_render = True
        render(os.path.join(out, "apercu-table.png"), objs, table=(W_in * IN, D_in * IN), cam=(0.0, -0.95, 0.85), res=(1600, 1100))
    return counts, notes

# ---------------------------------------------------------------- entrée
# Dans l'interface de Blender (fichier vide : le script efface la scène) :
#   mode "kit" ou "carte" ; layout = chemin du JSON exporté par l'Atelier.
PARAMS_GUI = {"mode": "kit", "layout": "", "out": os.path.expanduser("~/cytherea_stl"), "bed": 220, "render": False}

def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None
    if argv is None and bpy.app.background is False:
        P = PARAMS_GUI
        out = P["out"]
        if P["mode"] == "kit":
            make_kit(out, P["bed"], P["render"])
        else:
            make_from_layout(bpy.path.abspath(P["layout"]), out, P["bed"], P["render"])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", action="store_true")
    ap.add_argument("--layout")
    ap.add_argument("--out", default="cytherea_stl")
    ap.add_argument("--bed", type=float, default=220)
    ap.add_argument("--render", action="store_true")
    a = ap.parse_args(argv or [])
    if a.layout:
        c, n = make_from_layout(a.layout, a.out, a.bed, a.render)
        print("pièces :", c, "| non générés :", n)
    else:
        make_kit(a.out, a.bed, a.render)
        print("kit écrit dans", a.out)

if __name__ == "__main__":
    main()
