"""Grocery knowledge: store sections, default walk orders, item keywords, staples.

Pure data and string functions (no database, no network), so the migration
and the tests can use them directly.
"""

from __future__ import annotations

import re

# Canonical sections. Every store list orders these in its own walk order.
SECTIONS = [
    "Produce",
    "Bakery",
    "Deli",
    "Meat & Seafood",
    "Dairy & Eggs",
    "Breakfast & Cereal",
    "Pasta, Rice & Grains",
    "Canned & Dry Goods",
    "International",
    "Baking",
    "Spices & Condiments",
    "Snacks",
    "Beverages",
    "Frozen",
    "Household",
    "Personal Care",
    "Pharmacy",
    "Pet",
    "Other",
]

# Starting walk orders; rearranged in the app (Arrange sections).
DEFAULT_ORDER = {
    "Meijer": SECTIONS,
    "Costco": [
        "Household",
        "Personal Care",
        "Pharmacy",
        "Snacks",
        "Beverages",
        "Canned & Dry Goods",
        "Baking",
        "Spices & Condiments",
        "Breakfast & Cereal",
        "Pasta, Rice & Grains",
        "International",
        "Produce",
        "Meat & Seafood",
        "Deli",
        "Dairy & Eggs",
        "Bakery",
        "Frozen",
        "Pet",
        "Other",
    ],
}

# keyword -> section. Matched as whole words on the normalized item name,
# longest keyword first, so "chicken broth" beats "chicken".
_KW: dict[str, list[str]] = {
    "Produce": """apple apples banana bananas orange oranges lemon lemons lime limes grape grapes
        berries strawberries blueberries raspberries blackberries avocado avocados tomato tomatoes
        potato potatoes onion onions shallot shallots garlic ginger lettuce spinach kale arugula
        cabbage carrot carrots celery cucumber cucumbers zucchini squash pepper peppers bell
        jalapeño jalapeno jalapenos serrano broccoli cauliflower mushrooms mushroom herbs cilantro
        parsley basil mint dill rosemary thyme sage chives scallion scallions leek leeks beet beets
        corn pear pears peach peaches plum plums melon watermelon cantaloupe pineapple mango
        mangoes kiwi cherries radish radishes asparagus eggplant fennel bok choy greens collard
        collards chard sprouts lemongrass shiitake salad cherry""",
    "Bakery": """bread baguette rolls buns bagels bagel croissants tortillas pita naan muffins
        sandwich bread hamburger buns hot dog buns""",
    "Deli": """deli ham turkey breast salami prosciutto pastrami hummus rotisserie""",
    "Meat & Seafood": """chicken beef pork steak ground beef ground turkey bacon sausage
        sausages ribs short ribs brisket roast lamb veal salmon shrimp fish cod tilapia tuna steak
        scallops crab lobster thighs drumsticks wings chuck sirloin tenderloin chops corned beef
        ham hock andouille chorizo""",
    "Dairy & Eggs": """milk eggs egg butter cheese cheddar mozzarella parmesan parmigiano
        feta ricotta yogurt cream sour cream half-and-half heavy cream cream cheese cottage
        buttermilk gruyère gruyere pecorino goat cheese mascarpone""",
    "Breakfast & Cereal": """cereal oatmeal oats granola pancake mix syrup maple syrup
        pop tarts""",
    "Pasta, Rice & Grains": """pasta spaghetti penne rigatoni macaroni noodles lasagna rice
        quinoa barley farro couscous polenta orzo gnocchi ramen egg noodles linguine fettuccine""",
    "Canned & Dry Goods": """beans black beans chickpeas lentils broth stock chicken broth
        beef broth vegetable broth canned tomatoes tomato paste tomato sauce crushed tomatoes
        diced tomatoes soup peanut butter jelly jam tuna can coconut milk salsa nuts almonds
        walnuts pecans peanuts raisins""",
    "International": """soy sauce fish sauce hoisin sriracha gochujang miso curry paste
        sesame oil rice vinegar mirin tahini kimchi""",
    "Baking": """flour sugar brown sugar powdered sugar baking soda baking powder yeast
        vanilla chocolate chips cocoa cornstarch cornmeal""",
    "Spices & Condiments": """salt black pepper pepper spice spices cumin paprika oregano
        chili powder
        cinnamon nutmeg red-pepper flakes bay leaves ketchup mustard dijon mayonnaise mayo
        vinegar olive oil vegetable oil oil honey hot sauce relish dressing seasoning""",
    "Snacks": """chips crackers pretzels popcorn cookies candy snack snacks granola bars""",
    "Beverages": """water sparkling water soda juice coffee tea beer wine seltzer kombucha""",
    "Frozen": """frozen ice cream pizza peas frozen peas waffles""",
    "Household": """paper towels toilet paper napkins foil aluminum foil plastic wrap trash bags
        dish soap detergent laundry sponges batteries light bulbs cleaner bleach ziploc""",
    "Personal Care": """shampoo conditioner soap toothpaste toothbrush deodorant lotion razors
        tissues sunscreen""",
    "Pharmacy": """vitamins ibuprofen tylenol advil medicine bandaids band-aids allergy""",
    "Pet": """dog food cat food litter pet treats""",
}
KEYWORDS: dict[str, str] = {}

# Multi-word keywords inside the strings above; anything not listed here is
# split into single words.
_PHRASES = [
    "bok choy",
    "sour cream",
    "heavy cream",
    "cream cheese",
    "goat cheese",
    "half-and-half",
    "sandwich bread",
    "hamburger buns",
    "hot dog buns",
    "ground beef",
    "ground turkey",
    "short ribs",
    "corned beef",
    "ham hock",
    "turkey breast",
    "pancake mix",
    "maple syrup",
    "pop tarts",
    "egg noodles",
    "black beans",
    "chicken broth",
    "beef broth",
    "vegetable broth",
    "canned tomatoes",
    "tomato paste",
    "tomato sauce",
    "crushed tomatoes",
    "diced tomatoes",
    "peanut butter",
    "coconut milk",
    "soy sauce",
    "fish sauce",
    "curry paste",
    "sesame oil",
    "rice vinegar",
    "brown sugar",
    "powdered sugar",
    "baking soda",
    "baking powder",
    "chocolate chips",
    "chili powder",
    "black pepper",
    "red-pepper flakes",
    "bay leaves",
    "olive oil",
    "vegetable oil",
    "hot sauce",
    "sparkling water",
    "ice cream",
    "frozen peas",
    "paper towels",
    "toilet paper",
    "aluminum foil",
    "plastic wrap",
    "trash bags",
    "dish soap",
    "light bulbs",
    "dog food",
    "cat food",
    "pet treats",
    "granola bars",
]

for _section, _words in _KW.items():
    text = " " + " ".join(_words.split()) + " "
    for phrase in _PHRASES:
        if f" {phrase} " in text:
            KEYWORDS.setdefault(phrase, _section)
            text = text.replace(f" {phrase} ", " ")
    for w in text.split():
        KEYWORDS.setdefault(w, _section)
# Phrases override single words regardless of which section listed them first.
for _section, _words in _KW.items():
    joined = " " + " ".join(_words.split()) + " "
    for phrase in _PHRASES:
        if f" {phrase} " in joined:
            KEYWORDS[phrase] = _section

_SORTED_KEYWORDS = sorted(KEYWORDS, key=len, reverse=True)

# Things most kitchens already have: unticked by default when adding a recipe.
STAPLES = {
    "salt",
    "kosher salt",
    "sea salt",
    "pepper",
    "black pepper",
    "freshly ground pepper",
    "freshly ground black pepper",
    "water",
    "hot water",
    "ice",
    "olive oil",
    "extra-virgin olive oil",
    "vegetable oil",
    "neutral oil",
    "canola oil",
    "oil",
    "sugar",
    "granulated sugar",
    "flour",
    "all-purpose flour",
    "butter",
    "unsalted butter",
    "baking soda",
    "baking powder",
    "cooking spray",
    "nonstick cooking spray",
}

_UNITS = r"""cups?|c\.|tablespoons?|tbsps?\.?|teaspoons?|tsps?\.?|pounds?|lbs?\.?|ounces?|oz\.?|
    grams?|g|kilograms?|kg|ml|milliliters?|liters?|l|quarts?|qts?|pints?|pts?|gallons?|cans?|
    jars?|packages?|pkgs?|boxes?|bunch(?:es)?|heads?|cloves?|sticks?|slices?|pieces?|sprigs?|
    stalks?|pinch(?:es)?|dash(?:es)?|large|medium|small|whole|handfuls?|bags?|bottles?|
    containers?|inch|inches|knobs?"""
_QTY = re.compile(
    r"^\s*(?:about\s+)?(?:[\d½¼¾⅓⅔⅛.,/\-–\s]+(?:to\s+[\d½¼¾⅓⅔⅛.,/]+\s*)?)?"
    r"(?:\(\s*[^)]*\)\s*)?(?:(?:" + "".join(_UNITS.split()) + r")\b\.?\s*)*(?:of\s+)?",
    re.I,
)


def item_name(text: str) -> str:
    """'2 lb boneless chicken thighs, trimmed' -> 'boneless chicken thighs'."""
    s = text.strip().lstrip("-*•").strip()
    s = _QTY.sub("", s, count=1)
    s = re.sub(r"\([^)]*\)", " ", s)  # parentheticals
    s = s.split(",")[0]  # ", trimmed and cut into cubes"
    s = re.sub(r"\b(?:plus more|for serving|to taste|optional|divided)\b.*$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" .;:-")
    return s or text.strip()


def norm_key(text: str) -> str:
    """Key used to remember a section/aisle for an item."""
    return item_name(text).casefold()


def keyword_section(text: str) -> str | None:
    key = " " + re.sub(r"[^\w\s\-]", " ", norm_key(text)) + " "
    key = re.sub(r"\s+", " ", key)
    for kw in _SORTED_KEYWORDS:
        if f" {kw} " in key:
            return KEYWORDS[kw]
    return None


def is_staple(text: str) -> bool:
    return norm_key(text) in STAPLES
