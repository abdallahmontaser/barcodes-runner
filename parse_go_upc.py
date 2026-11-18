import re
from bs4 import BeautifulSoup


def clean_text(s):
    if s is None:
        return ''
    s = s.replace('\xa0', ' ')
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def section_text(soup, heading):
    """Text following an h2 whose text matches heading, until the next h2."""
    for h2 in soup.find_all(['h2', 'h3']):
        if clean_text(h2.get_text(' ', strip=True)).lower() == heading.lower():
            parent = h2.parent
            # gather siblings after the heading within its container
            parts = []
            node = h2.find_next_sibling()
            while node is not None:
                if node.name in ('h2', 'h3'):
                    break
                parts.append(node.get_text(' ', strip=True))
                node = node.find_next_sibling()
            if not parts and parent is not None:
                # heading may be alone in its div; take the parent's text minus heading
                txt = clean_text(parent.get_text(' ', strip=True))
                txt = clean_text(txt[len(clean_text(h2.get_text(' ', strip=True))):])
                if txt:
                    parts.append(txt)
            return clean_text(' '.join(parts))
    return ''


def parse_product(html, queried_code=''):
    soup = BeautifulSoup(html, 'lxml')
    title_tag = soup.find('title')
    title_text = clean_text(title_tag.get_text()) if title_tag else ''
    text_all = soup.get_text(' ', strip=True)

    if 'Easy now!' in text_all:
        return {'status': 'rate_limited', 'queried_code': queried_code}

    name_tag = soup.select_one('h1.product-name')
    if name_tag is None:
        if 'Product Not Found' in title_text or 'were not able to find a product' in text_all:
            return {'status': 'not_found', 'queried_code': queried_code, 'title': title_text}
        return {'status': 'unknown', 'queried_code': queried_code, 'title': title_text,
                'snippet': clean_text(text_all)[:300]}

    name = clean_text(name_tag.get_text(' ', strip=True))

    # Table metadata (EAN, UPC, ISBN, Brand, Category, etc.)
    table_rows = []
    meta = {}
    for tr in soup.select('table.table tr'):
        cells = tr.find_all(['td', 'th'])
        if len(cells) >= 2:
            label = clean_text(cells[0].get_text(' ', strip=True)).rstrip(':')
            value = clean_text(cells[1].get_text(' ', strip=True))
            if label:
                table_rows.append([label, value])
                meta.setdefault(label, value)

    # Structured badges
    structured = []
    for badge in soup.select('.product-details .structured-data .badge'):
        structured.append({
            'text': clean_text(badge.get_text(' ', strip=True)),
            'title': badge.get('title', ''),
            'class': ' '.join(badge.get('class', [])),
        })

    description = section_text(soup, 'Description')
    ingredients = section_text(soup, 'Ingredients')

    attributes = []
    for h2 in soup.find_all(['h2', 'h3']):
        if clean_text(h2.get_text(' ', strip=True)).lower() == 'additional attributes':
            node = h2.find_next_sibling()
            while node is not None and node.name in ('h2', 'h3'):
                break
            if node is not None:
                for li in node.find_all('li'):
                    label_tag = li.find('span', class_='metadata-label')
                    if label_tag:
                        label = clean_text(label_tag.get_text(' ', strip=True)).rstrip(':')
                        value = clean_text(li.get_text(' ', strip=True))
                        value = clean_text(value[len(clean_text(label_tag.get_text(' ', strip=True))):])
                        attributes.append({'name': label, 'value': value})
                    else:
                        attributes.append({'name': '', 'value': clean_text(li.get_text(' ', strip=True))})
            break

    merchants = []
    for h2 in soup.find_all(['h2', 'h3']):
        if 'merchants' in clean_text(h2.get_text(' ', strip=True)).lower():
            parent = h2.parent
            if parent:
                for a in parent.find_all('a'):
                    href = a.get('href', '')
                    label = clean_text(a.get_text(' ', strip=True))
                    if href and label:
                        merchants.append({'name': label, 'url': href})
            break

    images = []
    for img in soup.select('figure.product-image img, .product-image img'):
        src = img.get('src') or img.get('data-src')
        if src and src not in images:
            images.append(src)
    # any other product gallery images
    for img in soup.select('img'):
        src = img.get('src') or ''
        if src and ('go-upc.s3.amazonaws.com' in src or '/images/' in src) and src not in images:
            images.append(src)

    # Language detection on text fields
    arabic_re = re.compile(r'[\u0600-\u06FF]')
    latin_re = re.compile(r'[A-Za-z]')
    name_ar = name if arabic_re.search(name) else ''
    name_en = name if latin_re.search(name) and not arabic_re.search(name) else ''
    desc_ar = description if arabic_re.search(description) else ''
    desc_en = description if latin_re.search(description) and not arabic_re.search(description) else ''

    return {
        'status': 'found',
        'queried_code': queried_code,
        'title': title_text,
        'name': name,
        'name_ar': name_ar,
        'name_en': name_en,
        'brand': meta.get('Brand', ''),
        'category': meta.get('Category', ''),
        'ean': meta.get('EAN', ''),
        'upc': meta.get('UPC', ''),
        'isbn': meta.get('ISBN', ''),
        'gtin': meta.get('GTIN', ''),
        'description': description,
        'description_ar': desc_ar,
        'description_en': desc_en,
        'ingredients': ingredients,
        'attributes': attributes,
        'merchants': merchants,
        'images': images,
        'structured': structured,
        'table': table_rows,
    }


if __name__ == '__main__':
    import json
    import sys
    for path in sys.argv[1:]:
        html = open(path, encoding='utf-8', errors='replace').read()
        print(json.dumps(parse_product(html, path), ensure_ascii=False, indent=2)[:3000])
