from rosie.htmltext import extract

PAGE = """<html><head><title>Best Chili | Blog</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebPage","name":"x"},
 {"@type":["Recipe"],"name":"Best Chili","recipeYield":"6","recipeIngredient":["1 lb beef"],
  "recipeInstructions":[{"@type":"HowToStep","text":"Brown the beef."}],"image":"big.jpg"}]}
</script><style>.a{}</style></head>
<body><nav><ul><li>Home<li>About</ul></nav>
<article><h1>Best Chili</h1><p>My grandma &amp; me.</p><ul><li>1 lb beef</li></ul></article>
<footer>copyright</footer></body></html>"""


def test_extract_jsonld_and_text():
    ld, title, text = extract(PAGE)
    assert title == "Best Chili | Blog"
    assert ld == [
        {
            "name": "Best Chili",
            "recipeYield": "6",
            "recipeIngredient": ["1 lb beef"],
            "recipeInstructions": [{"@type": "HowToStep", "text": "Brown the beef."}],
        }
    ]
    assert "grandma & me" in text and "- 1 lb beef" in text
    assert "Home" not in text and "copyright" not in text and ".a{}" not in text


def test_extract_bad_jsonld_and_no_recipe():
    ld, _, text = extract('<script type="application/ld+json">{oops</script><p>Just text</p>')
    assert ld == [] and text == "Just text"
