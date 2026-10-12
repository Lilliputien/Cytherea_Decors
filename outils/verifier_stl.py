"""Contrôle des STL générés : étanchéité, dimensions et surplombs.

    python3 verifier_stl.py dossier [dossier…]

Surplomb = face tournée vers le bas (normale z < -0,71, soit plus de 45°) au-dessus
du plateau. Les battants imprimés à plat (« _a-plat ») ne sont contrôlés que pour
l'étanchéité. On rapporte, par pièce, l'aire en surplomb et la plus grande portée
d'un îlot de surplomb (un pont de quelques millimètres s'imprime sans support).
"""
import sys, os, glob
import numpy as np
import trimesh

def check(path):
    m = trimesh.load(path)
    ext = m.bounds[1] - m.bounds[0]
    res = {"fichier": os.path.basename(path), "etanche": bool(m.is_watertight), "volume_cm3": round(m.volume / 1000, 1),
           "dim_mm": tuple(round(float(v), 1) for v in ext), "surplomb_mm2": 0.0, "portee_max_mm": 0.0}
    if "_a-plat" in path:
        return res
    n, c = m.face_normals, m.triangles_center
    zmin = m.bounds[0][2]
    bad = np.where((n[:, 2] < -0.71) & (c[:, 2] > zmin + 0.5))[0]
    if len(bad):
        res["surplomb_mm2"] = round(float(m.area_faces[bad].sum()), 1)
        sub = m.submesh([bad], append=True)
        span = 0.0
        for part in sub.split(only_watertight=False):
            e = part.bounds[1] - part.bounds[0]
            span = max(span, float(min(e[0], e[1])))   # un pont se mesure dans sa plus petite largeur
        res["portee_max_mm"] = round(span, 1)
    return res

if __name__ == "__main__":
    rows = []
    for d in sys.argv[1:]:
        for p in sorted(glob.glob(os.path.join(d, "**", "*.stl"), recursive=True)):
            rows.append(check(p))
    for r in rows:
        flag = "" if r["etanche"] and r["portee_max_mm"] <= 14 else "  <-- à revoir"
        print(f'{r["fichier"]:<52} étanche={r["etanche"]!s:<5} {r["dim_mm"]} mm  vol={r["volume_cm3"]} cm³  '
              f'surplomb={r["surplomb_mm2"]} mm² portée={r["portee_max_mm"]} mm{flag}')
