import os
import time
import json
import logging
import schedule
import tweepy
from datetime import datetime
from dotenv import load_dotenv
from paapi5_python_sdk.api.default_api import DefaultApi
from paapi5_python_sdk.models.get_items_request import GetItemsRequest
from paapi5_python_sdk.models.get_items_resource import GetItemsResource
from paapi5_python_sdk.models.partner_type import PartnerType
from paapi5_python_sdk.rest import ApiException

# 環境変数の読み込み
load_dotenv()

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("amazon_tracker.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("amazon_tracker")

# PA-API設定
PA_API_KEY = os.getenv("PA_API_KEY")
PA_API_SECRET = os.getenv("PA_API_SECRET")
PARTNER_TAG = os.getenv("PARTNER_TAG")
MARKETPLACE = os.getenv("MARKETPLACE", "www.amazon.co.jp")

# X API設定
CONSUMER_KEY = os.getenv("TWITTER_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("TWITTER_CONSUMER_SECRET")
ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")

# トラッキング設定
TRACKING_PRODUCTS_FILE = "tracking_products.json"
TEMPLATES_FILE = "post_templates.json"


class AmazonTracker:
    def __init__(self):
        self.products = self.load_products()
        self.templates = self.load_templates()
        self.setup_twitter_api()
        self.setup_paapi()
        
    def setup_twitter_api(self):
        """Twitter APIの設定"""
        try:
            auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
            auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
            self.twitter_api = tweepy.API(auth)
            logger.info("Twitter API認証成功")
        except Exception as e:
            logger.error(f"Twitter API認証エラー: {e}")
            self.twitter_api = None
    
    def setup_paapi(self):
        """PA-APIの設定"""
        try:
            self.default_api = DefaultApi(
                access_key=PA_API_KEY,
                secret_key=PA_API_SECRET,
                host=MARKETPLACE,
                region="us-west-2"  # Amazonの推奨リージョン
            )
            logger.info("PA-API設定完了")
        except Exception as e:
            logger.error(f"PA-API設定エラー: {e}")
            self.default_api = None
    
    def load_products(self):
        """追跡商品リストを読み込む"""
        try:
            with open(TRACKING_PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.info(f"{TRACKING_PRODUCTS_FILE}が見つかりません。新規作成します。")
            return []
        except json.JSONDecodeError:
            logger.error(f"{TRACKING_PRODUCTS_FILE}の解析に失敗しました。")
            return []
    
    def load_templates(self):
        """投稿テンプレートを読み込む"""
        try:
            with open(TEMPLATES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.info(f"{TEMPLATES_FILE}が見つかりません。デフォルトテンプレートを使用します。")
            # デフォルトテンプレートを作成
            default_templates = {
                "default": {
                    "title": "【Amazon価格・在庫変動】",
                    "price_up": "⬆️ {diff:,}円上昇 ({percent:.1f}%)",
                    "price_down": "⬇️ {diff:,}円下落 ({percent:.1f}%)",
                    "availability_change": "在庫状況: {old} → {new}",
                    "current_price": "現在価格: {price:,}円",
                    "footer": ""
                },
                "flash_sale": {
                    "title": "🔥【緊急値下げ速報】🔥",
                    "price_up": "値上げ: +{diff:,}円 (+{percent:.1f}%)",
                    "price_down": "【値下げ】{diff:,}円引き ({percent:.1f}%オフ)",
                    "availability_change": "在庫状況変更: {old} → {new}",
                    "current_price": "✅ 特価: {price:,}円",
                    "footer": "#お買い得 #タイムセール"
                }
            }
            # テンプレートを保存
            with open(TEMPLATES_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_templates, f, ensure_ascii=False, indent=2)
            return default_templates
    
    def save_products(self):
        """追跡商品リストを保存する"""
        with open(TRACKING_PRODUCTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.products, f, ensure_ascii=False, indent=2)
        logger.info("商品リストを保存しました")
    
    def save_templates(self):
        """テンプレートを保存する"""
        with open(TEMPLATES_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.templates, f, ensure_ascii=False, indent=2)
        logger.info("テンプレートを保存しました")
    
    def add_template(self, name, template_data):
        """新しいテンプレートを追加"""
        self.templates[name] = template_data
        self.save_templates()
        logger.info(f"新しいテンプレート「{name}」を追加しました")
    
    def add_product(self, asin):
        """商品を追跡リストに追加（アフィリエイトリンク対応）"""
        if not self.default_api:
            logger.error("PA-API未設定のため商品を追加できません")
            return False
        
        # 商品情報を取得
        product_info = self.fetch_product_info([asin])
        if not product_info or asin not in product_info:
            logger.error(f"商品情報の取得に失敗: {asin}")
            return False
        
        item_info = product_info[asin]
        
        # 商品URLの作成（アフィリエイトリンク）
        url = item_info.get("detail_page_url", f"https://www.amazon.co.jp/dp/{asin}?tag={PARTNER_TAG}")
        
        # URLにアフィリエイトタグが含まれていない場合は追加
        if "?tag=" not in url and "&tag=" not in url:
            url_separator = "&" if "?" in url else "?"
            url = f"{url}{url_separator}tag={PARTNER_TAG}"
        
        # 商品情報を構築
        product = {
            "asin": asin,
            "name": item_info.get("title", f"商品 {asin}"),
            "url": url,
            "last_price": item_info.get("price"),
            "last_availability": item_info.get("availability"),
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "price_history": [
                {
                    "price": item_info.get("price"),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            ]
        }
        
        # 既存の商品リストに追加
        self.products.append(product)
        self.save_products()
        logger.info(f"新商品を追加しました: {product['name']} ({asin})")
        return True
    
    def fetch_product_info(self, asin_list):
        """PA-APIから商品情報を取得"""
        if not self.default_api:
            logger.error("PA-API未設定のため商品情報を取得できません")
            return None
        
        try:
            # リクエスト設定
            get_items_request = GetItemsRequest(
                partner_tag=PARTNER_TAG,
                partner_type=PartnerType.ASSOCIATES,
                marketplace=MARKETPLACE,
                item_ids=asin_list,
                resources=[
                    GetItemsResource.ITEM_INFO_TITLE,
                    GetItemsResource.OFFERS_LISTINGS_PRICE,
                    GetItemsResource.OFFERS_LISTINGS_AVAILABILITY_MESSAGE,
                    GetItemsResource.OFFERS_LISTINGS_AVAILABILITY_TYPE,
                    GetItemsResource.OFFERS_LISTINGS_DELIVERY_INFO_IS_PRIME_ELIGIBLE,
                    GetItemsResource.OFFERS_LISTINGS_SAVING_BASIS
                ]
            )
            
            # API呼び出し
            response = self.default_api.get_items(get_items_request)
            
            result = {}
            
            # レスポンス解析
            if response.items_result is not None:
                for item in response.items_result.items:
                    asin = item.asin
                    
                    # 価格情報
                    price = None
                    if (item.offers is not None and 
                        item.offers.listings is not None and 
                        len(item.offers.listings) > 0 and
                        item.offers.listings[0].price is not None):
                        price = int(float(item.offers.listings[0].price.amount))
                    
                    # 在庫情報
                    availability = "不明"
                    if (item.offers is not None and 
                        item.offers.listings is not None and 
                        len(item.offers.listings) > 0 and
                        item.offers.listings[0].availability is not None):
                        availability = item.offers.listings[0].availability.message
                    
                    # 商品タイトル
                    title = "不明"
                    if (item.item_info is not None and 
                        item.item_info.title is not None and
                        item.item_info.title.display_value is not None):
                        title = item.item_info.title.display_value
                    
                    # 商品ページURL（アフィリエイトリンク）
                    detail_url = f"https://www.amazon.co.jp/dp/{asin}?tag={PARTNER_TAG}"
                    if item.detail_page_url is not None:
                        # PA-APIから返されるURLには既にアソシエイトタグが含まれている
                        detail_url = item.detail_page_url
                    
                    # Primeステータス
                    is_prime = False
                    if (item.offers is not None and 
                        item.offers.listings is not None and 
                        len(item.offers.listings) > 0 and
                        item.offers.listings[0].delivery_info is not None and
                        item.offers.listings[0].delivery_info.is_prime_eligible is not None):
                        is_prime = item.offers.listings[0].delivery_info.is_prime_eligible
                    
                    result[asin] = {
                        "title": title,
                        "price": price,
                        "availability": availability,
                        "detail_page_url": detail_url,
                        "is_prime": is_prime
                    }
            
            return result
            
        except ApiException as e:
            logger.error(f"PA-API呼び出しエラー: {e}")
            return None
        except Exception as e:
            logger.error(f"商品情報取得エラー: {e}")
            return None
    
    def check_products(self):
        """全ての追跡商品の情報を更新"""
        if not self.products:
            logger.info("追跡商品がありません")
            return
        
        # ASINリストを作成（PA-APIは一度に10アイテムまで）
        asin_chunks = [
            [product["asin"] for product in self.products[i:i+10]]
            for i in range(0, len(self.products), 10)
        ]
        
        updated_products = {}
        
        # チャンク単位で情報取得
        for asin_list in asin_chunks:
            # API呼び出し制限を考慮して待機
            time.sleep(1)
            
            product_info = self.fetch_product_info(asin_list)
            if not product_info:
                logger.error(f"商品情報の取得に失敗: {', '.join(asin_list)}")
                continue
            
            updated_products.update(product_info)
        
        # 変動を検出して通知
        for product in self.products:
            asin = product["asin"]
            
            if asin not in updated_products:
                logger.warning(f"商品情報が取得できませんでした: {asin}")
                continue
            
            item_info = updated_products[asin]
            current_price = item_info.get("price")
            current_availability = item_info.get("availability")
            last_price = product["last_price"]
            last_availability = product["last_availability"]
            
            changes = []
            
            # 価格変動の検知
            if current_price is not None and last_price is not None and current_price != last_price:
                diff = current_price - last_price
                diff_percent = (diff / last_price) * 100 if last_price > 0 else 0
                price_change = {
                    "price": current_price,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                product["price_history"].append(price_change)
                
                change_text = f"⬆️ {abs(diff):,}円上昇" if diff > 0 else f"⬇️ {abs(diff):,}円下落"
                percent_text = f"({abs(diff_percent):.1f}%)"
                
                changes.append(f"価格変動: {change_text} {percent_text}")
                logger.info(f"価格変動検知: {product['name']} - {last_price:,}円 → {current_price:,}円 ({change_text})")
            
            # 在庫状況変動の検知
            if current_availability is not None and last_availability is not None and current_availability != last_availability:
                changes.append(f"在庫状況: {last_availability} → {current_availability}")
                logger.info(f"在庫変動検知: {product['name']} - {last_availability} → {current_availability}")
            
            # 変動があれば投稿
            if changes:
                # 商品情報を更新
                product["last_price"] = current_price
                product["last_availability"] = current_availability
                product["last_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # X投稿
                self.post_to_twitter(product, changes)
            else:
                logger.info(f"変動なし: {product['name']}")
        
        # 変更を保存
        self.save_products()
    
    def post_to_twitter(self, product, changes):
        """Xに投稿（テンプレート対応・アフィリエイトリンク付き）"""
        if not self.twitter_api:
            logger.error("Twitter API未設定のため投稿できません")
            return
        
        try:
            import re
            name = product["name"]
            asin = product["asin"]
            url = product["url"]
            current_price = product["last_price"]
            
            # URLにアフィリエイトタグが含まれていない場合は追加
            if "?tag=" not in url and "&tag=" not in url:
                url_separator = "&" if "?" in url else "?"
                url = f"{url}{url_separator}tag={PARTNER_TAG}"
            
            # 使用するテンプレートを選択（今回はデフォルト）
            # 商品カテゴリや価格変動率に応じて異なるテンプレートを選択することも可能
            template_name = "default"
            
            # 価格変動が10%以上の値下げの場合はフラッシュセールテンプレートを使用
            if any("価格変動" in change for change in changes):
                for change in changes:
                    if "価格変動" in change and "下落" in change:
                        # 価格変動の割合を抽出
                        match = re.search(r'\((\d+\.\d+)%\)', change)
                        if match and float(match.group(1)) >= 10.0:
                            template_name = "flash_sale"
                            break
            
            template = self.templates.get(template_name, self.templates["default"])
            
            # 投稿文を作成
            post = f"{template['title']}\n{name}\n\n"
            
            for change in changes:
                if "価格変動" in change:
                    if "上昇" in change:
                        # 価格上昇の場合
                        diff_match = re.search(r'(\d+,?\d*)円上昇', change)
                        percent_match = re.search(r'\((\d+\.\d+)%\)', change)
                        
                        if diff_match and percent_match:
                            diff = int(diff_match.group(1).replace(',', ''))
                            percent = float(percent_match.group(1))
                            post += f"・{template['price_up'].format(diff=diff, percent=percent)}\n"
                    elif "下落" in change:
                        # 価格下落の場合
                        diff_match = re.search(r'(\d+,?\d*)円下落', change)
                        percent_match = re.search(r'\((\d+\.\d+)%\)', change)
                        
                        if diff_match and percent_match:
                            diff = int(diff_match.group(1).replace(',', ''))
                            percent = float(percent_match.group(1))
                            post += f"・{template['price_down'].format(diff=diff, percent=percent)}\n"
                elif "在庫状況" in change:
                    # 在庫状況変化の場合
                    status_match = re.search(r'在庫状況: (.*) → (.*)', change)
                    if status_match:
                        old_status = status_match.group(1)
                        new_status = status_match.group(2)
                        post += f"・{template['availability_change'].format(old=old_status, new=new_status)}\n"
                else:
                    # その他の変化はそのまま追加
                    post += f"・{change}\n"
            
            if current_price:
                post += f"\n{template['current_price'].format(price=current_price)}\n"
            
            # フッターがあれば追加
            if template.get('footer'):
                post += f"\n{template['footer']}\n"
                
            post += f"\n{url}"
            
            # 投稿文が280文字を超える場合は調整
            if len(post) > 280:
                # 商品名を短縮
                name_limit = max(10, len(name) - (len(post) - 270))
                short_name = name[:name_limit] + "..."
                post = post.replace(name, short_name)
            
            # それでも長い場合はさらに調整
            if len(post) > 280:
                post = post[:277] + "..."
            
            # 投稿
            self.twitter_api.update_status(post)
            logger.info(f"Xに投稿しました: {post[:50]}...")
            
        except Exception as e:
            logger.error(f"X投稿エラー: {e}")


def main():
    tracker = AmazonTracker()
    
    # コマンドライン引数での処理
    import argparse
    parser = argparse.ArgumentParser(description='Amazon商品の価格・在庫変動を監視')
    parser.add_argument('--add', help='ASINを指定して商品を追加')
    parser.add_argument('--check', action='store_true', help='今すぐ全商品をチェック')
    parser.add_argument('--interval', type=int, default=60, help='チェック間隔（分）')
    parser.add_argument('--add-template', nargs=2, metavar=('NAME', 'JSON_FILE'), help='新しい投稿テンプレートを追加')
    args = parser.parse_args()
    
    if args.add:
        tracker.add_product(args.add)
    elif args.check:
        tracker.check_products()
    elif args.add_template:
        template_name = args.add_template[0]
        template_file = args.add_template[1]
        try:
            with open(template_file, 'r', encoding='utf-8') as f:
                template_data = json.load(f)
                tracker.add_template(template_name, template_data)
                logger.info(f"テンプレート '{template_name}' を追加しました")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"テンプレートの追加に失敗しました: {e}")
    else:
        # 定期実行のスケジュール設定
        schedule.every(args.interval).minutes.do(tracker.check_products)
        logger.info(f"定期監視を開始しました。{args.interval}分ごとに実行されます。")
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("プログラムを終了します。")

if __name__ == "__main__":
    main()
