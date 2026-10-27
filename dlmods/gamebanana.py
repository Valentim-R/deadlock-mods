"""Cliente da API pública do GameBanana (apiv11), só o que o app usa."""
import re

from . import net

API = "https://gamebanana.com/apiv11"
GAME_ID = 20948  # Deadlock

SORTS = {
    "Mais recentes": "Generic_Newest",
    "Atualizados recentemente": "Generic_LatestUpdated",
    "Mais baixados": "Generic_MostDownloaded",
    "Mais curtidos": "Generic_MostLiked",
    "Mais vistos": "Generic_MostViewed",
}

# A busca por texto só aceita estas ordens (e ignora filtro de categoria).
SEARCH_SORTS = {
    "Relevância": "best_match",
    "Popularidade": "popularity",
}

MOD_URL = re.compile(r"gamebanana\.com/mods/(\d+)")


def _summary(r):
    images = (r.get("_aPreviewMedia") or {}).get("_aImages") or []
    thumb = None
    if images:
        img = images[0]
        thumb = f"{img['_sBaseUrl']}/{img.get('_sFile220') or img['_sFile']}"
    return {
        "id": str(r["_idRow"]),
        "name": r["_sName"],
        "author": (r.get("_aSubmitter") or {}).get("_sName", ""),
        "category": (r.get("_aRootCategory") or {}).get("_sName", ""),
        "hero": (r.get("_aSubCategory") or {}).get("_sName", ""),
        "thumb": thumb,
        "likes": r.get("_nLikeCount", 0),
        "views": r.get("_nViewCount", 0),
        "modified": r.get("_tsDateModified", 0),
        "nsfw": bool(r.get("_bHasContentRatings")),
        "obsolete": bool(r.get("_bIsObsolete")),
        "featured": bool(r.get("_bWasFeatured")),
    }


def categories(parent=None):
    params = {"_sSort": "a_to_z", "_bShowEmpty": "true"}
    if parent:
        params["_idCategoryRow"] = parent
    else:
        params["_idGameRow"] = GAME_ID
    return [{"id": c["_idRow"], "name": c["_sName"], "count": c.get("_nItemCount", 0),
             "children": c.get("_nCategoryCount", 0), "icon": c.get("_sIconUrl")} for c in net.get_json(f"{API}/Mod/Categories", params)]


def browse(page=1, sort="Generic_Newest", category=None, featured=False, per_page=30):
    """Lista mods do Deadlock. Retorna (mods, total, acabou)."""
    params = {"_nPage": page, "_nPerpage": per_page, "_sSort": sort, "_aFilters[Generic_Game]": GAME_ID}
    if category:
        params["_aFilters[Generic_Category]"] = category
    if featured:
        params["_aFilters[Generic_WasFeatured]"] = "true"
    d = net.get_json(f"{API}/Mod/Index", params)
    meta = d["_aMetadata"]
    return [_summary(r) for r in d["_aRecords"]], meta["_nRecordCount"], meta["_bIsComplete"]


def search(text, page=1, order="best_match"):
    params = {"_sModelName": "Mod", "_sSearchString": text, "_idGameRow": GAME_ID, "_nPage": page, "_sOrder": order}
    d = net.get_json(f"{API}/Util/Search/Results", params)
    meta = d["_aMetadata"]
    return [_summary(r) for r in d["_aRecords"]], meta["_nRecordCount"], meta["_bIsComplete"]


def details(mod_id):
    r = net.get_json(f"{API}/Mod/{mod_id}/ProfilePage")
    out = _summary(r)
    cat = r.get("_aCategory") or {}
    sup = r.get("_aSuperCategory") or {}
    if sup:   # Skins > Yamato: categoria raiz em cima, herói embaixo
        out["category"], out["hero"] = sup.get("_sName", ""), cat.get("_sName", "")
    else:
        out["category"] = cat.get("_sName", out["category"])
    images = (r.get("_aPreviewMedia") or {}).get("_aImages") or []
    out.update({
        "url": r.get("_sProfileUrl"),
        "html": r.get("_sText") or "",
        "version": r.get("_sVersion") or "",
        "downloads": r.get("_nDownloadCount", 0),
        "images": [f"{i['_sBaseUrl']}/{i.get('_sFile530') or i['_sFile']}" for i in images],
        "files": [{
            "file_id": str(f["_idRow"]),
            "name": f["_sFile"],
            "size": f.get("_nFilesize", 0),
            "date": f.get("_tsDateAdded", 0),
            "md5": f.get("_sMd5Checksum"),
            "description": f.get("_sDescription") or "",
            "url": f.get("_sDownloadUrl") or f"https://gamebanana.com/dl/{f['_idRow']}",
            "archived": bool(f.get("_bIsArchived")),
        } for f in r.get("_aFiles") or []],
    })
    if out["images"]:
        out["thumb"] = f"{images[0]['_sBaseUrl']}/{images[0].get('_sFile220') or images[0]['_sFile']}"
    return out
