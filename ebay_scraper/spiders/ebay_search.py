import scrapy
import json
import re
from urllib.parse import urlencode, urlparse, parse_qs, urljoin
from ebay_scraper.items import EbaySearchItem
from datetime import datetime
import logging


class EbaySearchSpider(scrapy.Spider):
    name = 'ebay_search'
    allowed_domains = ['ebay.com', 'ebay.co.uk', 'ebay.de', 'ebay.ca', 'ebay.com.au', 'proxy.scrapeops.io']
    
    custom_settings = {
        'DOWNLOAD_DELAY': 2,
        'RANDOMIZE_DOWNLOAD_DELAY': True,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 4,
    }
    
    def __init__(self, query=None, max_results=50, site='US', condition=None, 
                 min_price=None, max_price=None, sort='BestMatch', *args, **kwargs):
        super(EbaySearchSpider, self).__init__(*args, **kwargs)
        
        self.search_query = query or 'laptop computer'
        self.max_results = int(max_results)
        self.site = site.upper()
        self.condition = condition
        self.min_price = min_price
        self.max_price = max_price
        self.sort = sort
        self.results_count = 0
        self.current_page = 1
        
        # eBay site URLs
        self.site_urls = {
            'US': 'https://www.ebay.com',
            'UK': 'https://www.ebay.co.uk',
            'DE': 'https://www.ebay.de',
            'CA': 'https://www.ebay.ca',
            'AU': 'https://www.ebay.com.au',
            'FR': 'https://www.ebay.fr',
            'IT': 'https://www.ebay.it',
            'ES': 'https://www.ebay.es',
        }
        
        self.base_url = self.site_urls.get(self.site, self.site_urls['US'])
        
        self.logger.info(f"Starting eBay search for: '{self.search_query}' on {self.site}")
        self.logger.info(f"Max results to scrape: {self.max_results}")
    
    async def start(self):
        # Scrapy 2.13+ entry point; start_requests() is kept for older versions.
        for request in self.start_requests():
            yield request

    def start_requests(self):
        """Generate initial search requests"""
        search_params = {
            '_nkw': self.search_query,  # Search query
            '_sop': self._get_sort_value(self.sort),  # Sort option
        }
        
        # Add condition filter
        if self.condition:
            search_params['LH_ItemCondition'] = self._get_condition_value(self.condition)
        
        # Add price range
        if self.min_price:
            search_params['_udlo'] = self.min_price
        if self.max_price:
            search_params['_udhi'] = self.max_price
        
        # Add other useful parameters
        search_params.update({
            '_from': 'R40',  # Results per page
            '_sacat': '0',   # All categories
            'rt': 'nc',      # No cache
            '_dcat': '1',    # Include description in search
        })
        
        search_url = f"{self.base_url}/sch/i.html?{urlencode(search_params)}"
        
        yield scrapy.Request(
            url=search_url,
            callback=self.parse_search_results,
            meta={
                'search_query': self.search_query,
                'page': 1,
                'search_params': search_params
            },
            headers=self._get_headers()
        )
    
    def _get_headers(self):
        """Get appropriate headers for eBay requests"""
        return {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    
    def _get_sort_value(self, sort):
        """Convert sort option to eBay sort value"""
        sort_mapping = {
            'BestMatch': '12',
            'PricePlusShippingLowest': '15',
            'PricePlusShippingHighest': '16',
            'EndTimeSoonest': '1',
            'StartTimeNewest': '10',
            'DistanceNearest': '21',
        }
        return sort_mapping.get(sort, '12')
    
    def _get_condition_value(self, condition):
        """Convert condition to eBay condition value"""
        condition_mapping = {
            'new': '1000',
            'open_box': '1500',
            'used': '3000',
            'refurbished': '2000',
            'for_parts': '7000',
        }
        return condition_mapping.get(condition.lower(), '')
    
    def parse_search_results(self, response):
        """Parse eBay search results page"""
        self.logger.info(f"Parsing search results from: {response.url}")
        
        # Each organic result is an <li class="s-card"> directly under the results list.
        # Carousels, filters and pagination are <li class="srp-river-answer"> and are skipped.
        item_containers = response.css('ul.srp-results > li.s-card[data-listingid]')
        
        if not item_containers:
            self.logger.warning("No search result items found")
            return
        
        for container in item_containers:
            if self.results_count >= self.max_results:
                return
            
            try:
                item_data = self.extract_search_item_data(container, response.meta)
                if item_data:
                    self.results_count += 1
                    yield item_data
            except Exception as e:
                self.logger.error(f"Error extracting search item: {e}")
                continue
        
        # Check for next page
        if self.results_count < self.max_results:
            next_page_url = self.get_next_page_url(response)
            if next_page_url:
                self.current_page += 1
                yield scrapy.Request(
                    url=next_page_url,
                    callback=self.parse_search_results,
                    meta={
                        'search_query': response.meta['search_query'],
                        'page': self.current_page,
                        'search_params': response.meta['search_params']
                    },
                    headers=self._get_headers()
                )
    
    CONDITION_PATTERN = re.compile(
        r'^(Brand New|New|New \(Other\)|New with.*|Open Box|Pre-Owned|Used|'
        r'.*Refurbished|Parts Only|For parts.*)$',
        re.IGNORECASE,
    )
    SELLER_PATTERN = re.compile(r'^(\S+)\s+([\d.]+)% positive \(([\d.,]+K?)\)')
    
    def _texts(self, selector):
        """Joined, whitespace-normalised text of a selector"""
        return re.sub(r'\s+', ' ', ' '.join(selector.css('*::text').getall())).strip()
    
    def extract_search_item_data(self, container, meta):
        """Extract data from a single search result card (eBay's s-card layout)"""
        try:
            item = EbaySearchItem()
            
            item['product_id'] = container.attrib.get('data-listingid', '')
            item['listing_id'] = item['product_id']
            
            # Product title and URL
            title = container.css('.s-card__title .su-styled-text::text').get('')
            url = container.css('.su-card-container__header a.s-card__link::attr(href)').get('') \
                or container.css('a.s-card__link::attr(href)').get('')
            
            if not title or not url:
                return None

            # Skip placeholder or invalid items (item_id must be all digits and at least 9 digits)
            if not item['product_id'].isdigit() or len(item['product_id']) < 9:
                return None
            
            item['product_title'] = self._clean_title(title)
            # Drop eBay's tracking parameters
            item['product_url'] = url.split('?')[0]
            
            # Price: may be a range ("$500.00 to $700.00"); a discounted listing
            # shows the original price in a struck-through span next to it.
            prices = [p.strip() for p in container.css('.s-card__price::text').getall() if p.strip()]
            item['current_price'] = ' to '.join(prices)
            item['original_price'] = container.css('.strikethrough::text').get('').strip()
            
            # Condition and subtitles
            item['condition'] = ''
            for subtitle in container.css('.s-card__subtitle'):
                text = self._texts(subtitle)
                if self.CONDITION_PATTERN.match(text):
                    item['condition'] = text
                    break
            
            # Image
            item['main_image'] = container.css('img.s-card__image::attr(src)').get('') \
                or container.css('img.s-card__image::attr(data-defer-load)').get('')
            item['thumbnail_image'] = item['main_image']
            
            # The attribute rows are unlabelled, so classify them by their text.
            item['shipping_cost'] = ''
            item['seller_location'] = ''
            item['seller_name'] = ''
            item['seller_feedback_score'] = ''
            item['seller_feedback_percentage'] = ''
            item['bid_count'] = ''
            item['time_left'] = ''
            item['watchers'] = ''
            item['items_sold'] = ''
            item['buy_it_now_price'] = ''
            item['price_type'] = 'buy_it_now'
            item['listing_format'] = ''
            item['returns_accepted'] = False
            
            for row in container.css('.s-card__attribute-row'):
                text = self._texts(row)
                lower = text.lower()
                seller_match = self.SELLER_PATTERN.match(text)
                if seller_match:
                    item['seller_name'] = seller_match.group(1)
                    item['seller_feedback_percentage'] = seller_match.group(2)
                    item['seller_feedback_score'] = seller_match.group(3)
                elif lower.startswith('located in'):
                    item['seller_location'] = text[len('located in'):].strip()
                elif 'delivery' in lower or 'shipping' in lower:
                    item['shipping_cost'] = text
                elif re.search(r'\d+\s*bids?\b', lower):
                    item['bid_count'] = self._extract_bid_count(text)
                    item['price_type'] = 'auction'
                elif lower.endswith(' left') or re.search(r'\d+[dhm] \d+[hms] left', lower):
                    item['time_left'] = text
                elif 'watcher' in lower:
                    item['watchers'] = self._extract_number(text.replace(',', ''))
                elif re.search(r'\bsold\b', lower):
                    item['items_sold'] = self._extract_number(text.replace(',', ''))
                elif lower == 'buy it now':
                    item['listing_format'] = 'Buy It Now'
                elif lower == 'or best offer':
                    item['listing_format'] = 'Best Offer'
                elif 'returns' in lower:
                    item['returns_accepted'] = True
            
            # Special features
            item['fast_n_free'] = 'free' in item['shipping_cost'].lower()
            item['top_rated_seller'] = bool(container.css('.su-icon--legacy-top-rated-seller, .su-icon--top-rated-seller'))
            item['authenticity_guarantee'] = bool(container.css('.su-icon--legacy-authenticity-guarantee, .su-icon--authenticity-guarantee'))
            
            # Search context
            item['search_query'] = meta['search_query']
            item['search_position'] = self.results_count + 1
            item['search_page'] = meta['page']
            item['search_sort'] = self.sort
            
            # Additional fields with defaults
            item['discount_percentage'] = ''
            item['brand'] = ''
            item['model'] = ''
            item['category'] = ''
            item['subcategory'] = ''
            item['seller_id'] = ''
            item['seller_verified'] = False
            item['quantity_available'] = ''
            item['listing_type'] = item['price_type']
            item['ebay_plus'] = item['top_rated_seller']
            item['sponsored'] = False
            item['reserve_met'] = ''
            item['end_time'] = ''
            item['ships_from'] = item['seller_location']
            item['ships_to'] = ''
            item['handling_time'] = ''
            item['shipping_type'] = ''
            item['return_period'] = ''
            item['image_count'] = 1 if item['main_image'] else 0
            
            self.logger.info(f"Extracted item: {item['product_title'][:50]}... - {item['current_price']}")
            return item
            
        except Exception as e:
            self.logger.error(f"Error extracting search item data: {e}")
            return None
    
    def _clean_title(self, title):
        """Clean product title"""
        if not title:
            return ''
        
        # Remove 'New Listing' prefix
        title = re.sub(r'^New Listing\s*', '', title, flags=re.IGNORECASE)
        
        # Remove extra whitespace
        title = re.sub(r'\s+', ' ', title).strip()
        
        return title
    
    def _extract_item_id(self, url):
        """Extract eBay item ID from URL"""
        try:
            patterns = [
                r'/itm/(\d+)',
                r'/itm/[^/]+/(\d+)',
                r'item=(\d+)',
                r'ItemID=(\d+)'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, url)
                if match:
                    return match.group(1)
            
            return ''
        except:
            return ''
    
    def _extract_bid_count(self, text):
        """Extract number of bids from text"""
        try:
            match = re.search(r'(\d+)\s*bid', text, re.IGNORECASE)
            return match.group(1) if match else ''
        except:
            return ''
    
    def _extract_price_from_text(self, text):
        """Extract price from text"""
        try:
            # Look for price patterns like $123.45
            price_match = re.search(r'[\$£€¥]\s*([0-9,]+\.?\d*)', text)
            return price_match.group(0) if price_match else ''
        except:
            return ''
    
    def _extract_number(self, text):
        """Extract number from text"""
        try:
            match = re.search(r'(\d+)', text)
            return match.group(1) if match else ''
        except:
            return ''
    
    def get_next_page_url(self, response):
        """Get URL for next page of results"""
        try:
            # Look for pagination next button
            next_link = response.css('.pagination__next::attr(href)').get()
            if next_link:
                return urljoin(response.url, next_link)
            
            # Alternative pagination selector
            next_link_alt = response.css('a.pager-next::attr(href)').get()
            if next_link_alt:
                return urljoin(response.url, next_link_alt)
            
            # Build next page URL manually
            current_params = response.meta.get('search_params', {})
            current_params['_pgn'] = self.current_page + 1
            
            base_url = f"{self.base_url}/sch/i.html"
            next_url = f"{base_url}?{urlencode(current_params)}"
            
            # Verify there are more results by checking result count
            result_count_text = response.css('.srp-controls__count-heading::text').get('')
            if result_count_text and 'of' in result_count_text:
                try:
                    # Extract total results and current position
                    match = re.search(r'(\d+[\d,]*)\s*-\s*(\d+[\d,]*)\s*of\s*(\d+[\d,]*)', result_count_text)
                    if match:
                        current_end = int(match.group(2).replace(',', ''))
                        total_results = int(match.group(3).replace(',', ''))
                        
                        if current_end < total_results:
                            return next_url
                except:
                    pass
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting next page URL: {e}")
            return None 