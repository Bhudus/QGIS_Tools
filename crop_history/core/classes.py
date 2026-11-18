"""Cropland Data Layer (CDL) categories: names, crop groups and fallback colours.

Codes and names follow the USDA NASS CDL legend. Colours are only a fallback:
the GeoTIFFs CropScape serves carry the official colour table, which is used
when present.
"""

# crop-group bits; a double crop carries two
CORN, SOY, SMALL_GRAIN, HAY, OTHER_CROP, FALLOW, NONCROP = 1, 2, 4, 8, 16, 32, 64
CROP_BITS = CORN | SOY | SMALL_GRAIN | HAY | OTHER_CROP | FALLOW
NO_DATA_CODES = (0, 81, 255)          # background, clouds/no data, fill

NAMES = {
    1: "Corn", 2: "Cotton", 3: "Rice", 4: "Sorghum", 5: "Soybeans", 6: "Sunflower",
    10: "Peanuts", 11: "Tobacco", 12: "Sweet Corn", 13: "Pop or Orn Corn", 14: "Mint",
    21: "Barley", 22: "Durum Wheat", 23: "Spring Wheat", 24: "Winter Wheat",
    25: "Other Small Grains", 26: "Dbl Crop WinWht/Soybeans", 27: "Rye", 28: "Oats",
    29: "Millet", 30: "Speltz", 31: "Canola", 32: "Flaxseed", 33: "Safflower",
    34: "Rape Seed", 35: "Mustard", 36: "Alfalfa", 37: "Other Hay/Non Alfalfa",
    38: "Camelina", 39: "Buckwheat", 41: "Sugarbeets", 42: "Dry Beans", 43: "Potatoes",
    44: "Other Crops", 45: "Sugarcane", 46: "Sweet Potatoes", 47: "Misc Vegs & Fruits",
    48: "Watermelons", 49: "Onions", 50: "Cucumbers", 51: "Chick Peas", 52: "Lentils",
    53: "Peas", 54: "Tomatoes", 55: "Caneberries", 56: "Hops", 57: "Herbs",
    58: "Clover/Wildflowers", 59: "Sod/Grass Seed", 60: "Switchgrass",
    61: "Fallow/Idle Cropland", 62: "Pasture/Grass", 63: "Forest", 64: "Shrubland",
    65: "Barren", 66: "Cherries", 67: "Peaches", 68: "Apples", 69: "Grapes",
    70: "Christmas Trees", 71: "Other Tree Crops", 72: "Citrus", 74: "Pecans",
    75: "Almonds", 76: "Walnuts", 77: "Pears", 81: "Clouds/No Data", 82: "Developed",
    83: "Water", 87: "Wetlands", 88: "Nonag/Undefined", 92: "Aquaculture",
    111: "Open Water", 112: "Perennial Ice/Snow", 121: "Developed/Open Space",
    122: "Developed/Low Intensity", 123: "Developed/Med Intensity",
    124: "Developed/High Intensity", 131: "Barren", 141: "Deciduous Forest",
    142: "Evergreen Forest", 143: "Mixed Forest", 152: "Shrubland",
    176: "Grassland/Pasture", 190: "Woody Wetlands", 195: "Herbaceous Wetlands",
    204: "Pistachios", 205: "Triticale", 206: "Carrots", 207: "Asparagus", 208: "Garlic",
    209: "Cantaloupes", 210: "Prunes", 211: "Olives", 212: "Oranges",
    213: "Honeydew Melons", 214: "Broccoli", 215: "Avocados", 216: "Peppers",
    217: "Pomegranates", 218: "Nectarines", 219: "Greens", 220: "Plums",
    221: "Strawberries", 222: "Squash", 223: "Apricots", 224: "Vetch",
    225: "Dbl Crop WinWht/Corn", 226: "Dbl Crop Oats/Corn", 227: "Lettuce",
    228: "Dbl Crop Triticale/Corn", 229: "Pumpkins", 230: "Dbl Crop Lettuce/Durum Wht",
    231: "Dbl Crop Lettuce/Cantaloupe", 232: "Dbl Crop Lettuce/Cotton",
    233: "Dbl Crop Lettuce/Barley", 234: "Dbl Crop Durum Wht/Sorghum",
    235: "Dbl Crop Barley/Sorghum", 236: "Dbl Crop WinWht/Sorghum",
    237: "Dbl Crop Barley/Corn", 238: "Dbl Crop WinWht/Cotton",
    239: "Dbl Crop Soybeans/Cotton", 240: "Dbl Crop Soybeans/Oats",
    241: "Dbl Crop Corn/Soybeans", 242: "Blueberries", 243: "Cabbage", 244: "Cauliflower",
    245: "Celery", 246: "Radishes", 247: "Turnips", 248: "Eggplants", 249: "Gourds",
    250: "Cranberries", 254: "Dbl Crop Barley/Soybeans",
}

_CORN = (1, 12, 13, 225, 226, 228, 237, 241)
_SOY = (5, 26, 239, 240, 241, 254)
_SMALL_GRAIN = (21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 39, 205, 225, 226, 228, 233, 234,
                235, 236, 237, 238, 240, 254)
_HAY = (36, 37, 58, 59, 60, 224)
_FALLOW = (61,)
_NONCROP = (62, 63, 64, 65, 82, 83, 87, 88, 92, 111, 112, 121, 122, 123, 124, 131, 141,
            142, 143, 152, 176, 190, 195)


def group_table():
    """256-entry list of crop-group bits per CDL code (0 = no data)."""
    t = [0] * 256
    for code in NAMES:
        if code in NO_DATA_CODES:
            continue
        b = 0
        if code in _CORN:
            b |= CORN
        if code in _SOY:
            b |= SOY
        if code in _SMALL_GRAIN:
            b |= SMALL_GRAIN
        if code in _HAY:
            b |= HAY
        if code in _FALLOW:
            b |= FALLOW
        if code in _NONCROP:
            b |= NONCROP
        if b == 0:
            b = OTHER_CROP
        t[code] = b
    return t


def name(code):
    return NAMES.get(int(code), "Class {}".format(int(code)))


# letters used in rotation sequences (dominant crop of each year)
def letter(code):
    code = int(code)
    if code in NO_DATA_CODES:
        return "-"
    special = {26: "W/S", 225: "W/C", 241: "C/S", 254: "B/S", 236: "W/Sg", 238: "W/Ct",
               237: "B/C", 226: "O/C", 240: "S/O"}
    if code in special:
        return special[code]
    base = {1: "C", 12: "C", 13: "C", 5: "S", 2: "Ct", 3: "R", 4: "Sg", 6: "Sf",
            21: "B", 22: "W", 23: "W", 24: "W", 25: "G", 27: "Ry", 28: "O", 205: "Tr",
            36: "A", 37: "H", 58: "H", 59: "H", 224: "H", 61: "F", 176: "P", 62: "P",
            41: "Sb", 42: "D", 43: "Po", 31: "Cn"}
    if code in base:
        return base[code]
    g = group_table()[code]
    return "N" if g & NONCROP else "X"


LETTER_KEY = ("C corn, S soybeans, W wheat, B barley, O oats, G other small grains, "
              "A alfalfa, H hay, Sg sorghum, Ct cotton, R rice, F fallow, P grass/pasture, "
              "X other crop, N non-crop, x/y double crop, - no data")

# fallback colours (r, g, b) for common classes
COLORS = {
    1: (255, 211, 0), 2: (255, 38, 38), 3: (0, 168, 228), 4: (255, 158, 11),
    5: (38, 112, 0), 6: (255, 255, 0), 12: (221, 165, 11), 13: (221, 165, 11),
    21: (226, 0, 124), 22: (137, 98, 84), 23: (216, 181, 107), 24: (165, 112, 0),
    25: (214, 158, 188), 26: (112, 112, 0), 27: (172, 0, 124), 28: (160, 89, 137),
    31: (209, 255, 0), 36: (255, 168, 227), 37: (165, 245, 141), 41: (168, 0, 228),
    42: (165, 0, 0), 43: (112, 38, 0), 59: (0, 175, 75), 61: (191, 191, 119),
    111: (77, 112, 163), 121: (156, 156, 156), 122: (156, 156, 156),
    123: (156, 156, 156), 124: (156, 156, 156), 131: (204, 191, 163),
    141: (147, 204, 147), 142: (147, 204, 147), 143: (147, 204, 147),
    152: (198, 214, 158), 176: (232, 255, 191), 190: (124, 175, 175),
    195: (124, 175, 175), 205: (214, 158, 188), 225: (255, 211, 0), 241: (255, 211, 0),
    254: (38, 112, 0),
}


def color(code):
    return COLORS.get(int(code), (180, 180, 180))


# rotation classes (raster codes)
ROTATIONS = {
    1: ("Corn-soybean", (230, 194, 0)),
    2: ("Continuous corn", (255, 140, 0)),
    3: ("Continuous soybean", (38, 112, 0)),
    4: ("Corn-soybean-small grain", (165, 112, 0)),
    5: ("Row crops with hay", (200, 120, 200)),
    6: ("Hay / forage", (255, 168, 227)),
    7: ("Other cropping", (140, 140, 140)),
    8: ("Mostly non-cropland", (147, 204, 147)),
}
