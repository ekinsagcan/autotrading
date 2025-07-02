import asyncio
import aiohttp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import ccxt.async_support as ccxt
import json
import time
from decimal import Decimal, ROUND_DOWN
import os
from datetime import datetime

# Logging ayarları
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global değişkenler (ÖNEMLİ: Gerçek uygulamada bunları güvenli bir şekilde yönetin)
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')  # Admin chat ID'si
# Bu adresleri ENV değişkenlerinden veya güvenli bir yapılandırma dosyasından alın
MEXC_WALLET_ADDRESS = os.getenv('MEXC_WALLET_ADDRESS', 'YOUR_MEXC_WALLET_ADDRESS_HERE')
GATE_IO_WALLET_ADDRESS = os.getenv('GATE_IO_WALLET_ADDRESS', 'YOUR_GATE_IO_WALLET_ADDRESS_HERE')


class ArbitrageBot:
    def __init__(self, telegram_token, gate_api_key, gate_secret, mexc_api_key, mexc_secret):
        self.telegram_token = telegram_token
        self.gate_api_key = gate_api_key
        self.gate_secret = gate_secret
        self.mexc_api_key = mexc_api_key
        self.mexc_secret = mexc_secret
        
        # Bot durumu
        self.is_running = False
        self.current_coin = "WHITE"
        self.min_profit_percentage = 2.0  # Minimum %2 kâr
        self.trade_amount_usdt = 100  # Varsayılan işlem miktarı
        self.check_interval = 30  # 30 saniye kontrol aralığı
        
        # Exchange bağlantıları
        self.gate_exchange = None
        self.mexc_exchange = None
        
        # İstatistikler
        self.stats = {
            'total_trades': 0,
            'successful_trades': 0,
            'total_profit': 0.0,
            'last_trade_time': None
        }
    
    async def initialize_exchanges(self):
        """Exchange bağlantılarını başlatır"""
        try:
            self.gate_exchange = ccxt.gateio({
                'apiKey': self.gate_api_key,
                'secret': self.gate_secret,
                'sandbox': False,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot', # Ensure spot trading
                    'createMarketBuyOrderRequiresPrice': False, # Düzeltme: Gate.io market buy için fiyat istemesin
                },
            })
            
            self.mexc_exchange = ccxt.mexc({
                'apiKey': self.mexc_api_key,
                'secret': self.mexc_secret,
                'sandbox': False,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot', # Ensure spot trading
                },
            })
            
            # Bağlantıları test et
            await self.gate_exchange.load_markets()
            await self.mexc_exchange.load_markets()
            
            logger.info("Exchange bağlantıları başarıyla kuruldu")
            return True
            
        except Exception as e:
            logger.error(f"Exchange bağlantısında hata: {e}")
            if ADMIN_CHAT_ID:
                # Admin'e hata bildirimi gönder
                await self.send_admin_message(f"🚨 **Hata: Exchange bağlantısı kurulamadı!**\n\nDetay: `{e}`")
            return False
    
    async def send_admin_message(self, message: str):
        """Admin chat ID'ye mesaj gönderir."""
        if ADMIN_CHAT_ID:
            try:
                # Telegram bot objesine dışarıdan erişmek için bir yol:
                # Bu bot sınıfının doğrudan Telegram Application objesine erişimi olmadığından,
                # bu fonksiyonu çağırırken bot objesini veya context'i parametre olarak geçmelisiniz.
                # Şimdilik, bot objesi main'de tanımlandığı için dışarıdan erişilemez.
                # Bu yüzden, monitoring_loop ve execute_arbitrage_trade'deki
                # `context.bot.send_message` kullanımları daha doğru.
                # Eğer başka yerlerden de admin mesajı göndermek isterseniz,
                # bot objesini __init__ içinde tutmak veya bir 'context' parametresi eklemek gerekebilir.
                # Örnek: await self.application.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='Markdown')
                pass
            except Exception as e:
                logger.error(f"Admin mesajı gönderme hatası: {e}")

    async def get_price_from_gate(self, symbol):
        """Gate.io'dan fiyat bilgisi alır"""
        try:
            ticker = await self.gate_exchange.fetch_ticker(f"{symbol}/USDT")
            return ticker['bid']  # Alış fiyatı (en yüksek alım emri)
        except Exception as e:
            logger.error(f"Gate.io fiyat alma hatası: {e}")
            if ADMIN_CHAT_ID:
                await self.send_admin_message(f"🚨 **Hata: Gate.io fiyat bilgisi alınamadı!**\n\nCoin: `{symbol}`\nDetay: `{e}`")
            return None
    
    async def get_price_from_mexc(self, symbol):
        """MEXC'den fiyat bilgisi alır"""
        try:
            ticker = await self.mexc_exchange.fetch_ticker(f"{symbol}/USDT")
            return ticker['ask']  # Satış fiyatı (en düşük satış emri)
        except Exception as e:
            logger.error(f"MEXC fiyat alma hatası: {e}")
            if ADMIN_CHAT_ID:
                await self.send_admin_message(f"🚨 **Hata: MEXC fiyat bilgisi alınamadı!**\n\nCoin: `{symbol}`\nDetay: `{e}`")
            return None
    
    async def get_transfer_fee(self, symbol):
        """Transfer ücreti hesaplar (yaklaşık)"""
        # BU DEĞERLER GERÇEK API'DEN ALINMALI VEYA GÜNCEL TUTULMALIDIR.
        # Sabit değerler piyasa koşullarına göre değişebilir ve yanlış kâr hesaplamalarına yol açabilir.
        transfer_fees = {
            'WHITE': 0.1,  # Örnek değer (Bu değeri Gate.io'nun WHITE çekme ücretinden kontrol edin)
            'BTC': 0.0005, # Örnek değer
            'ETH': 0.01, # Örnek değer
            'BNB': 0.01 # Örnek değer
        }
        return transfer_fees.get(symbol, 0.1) # Belirtilmeyen coinler için varsayılan ücret
    
    async def check_arbitrage_opportunity(self):
        """Arbitraj fırsatı kontrol eder"""
        try:
            gate_price = await self.get_price_from_gate(self.current_coin)
            mexc_price = await self.get_price_from_mexc(self.current_coin)
            
            if not gate_price or not mexc_price:
                logger.warning(f"Fiyat bilgileri eksik. Gate.io: {gate_price}, MEXC: {mexc_price}")
                return None
            
            transfer_fee = await self.get_transfer_fee(self.current_coin)
            
            # Kâr hesaplama
            # Gate.io'dan trade_amount_usdt karşılığı ne kadar coin alınabilir?
            coin_to_buy = Decimal(str(self.trade_amount_usdt)) / Decimal(str(gate_price))
            
            # Gate.io'da alış maliyeti (USDT cinsinden)
            buy_cost_usdt = Decimal(str(self.trade_amount_usdt))

            # MEXC'de satılacak coin miktarı (transfer ücreti düşülmüş hali)
            # Transfer ücreti genellikle coin cinsinden olur.
            # Örneğin, WHITE çekim ücreti 0.1 WHITE ise:
            # Coin_to_transfer = coin_to_buy - transfer_fee
            # Eğer transfer ücreti USDT cinsinden verilmişse, hesaplama farklılaşır.
            # Şimdilik, transfer ücretini USDT cinsinden, satış fiyatı üzerinden düşelim.
            # Bu kısım, gerçek transfer ücretlerinin nasıl hesaplandığına göre ayarlanmalı.
            
            # Basit bir yaklaşımla, transfer ücretini direkt coin miktarından düşelim
            # Eğer transfer_fee coin cinsindense
            coin_after_transfer_fee = coin_to_buy - Decimal(str(transfer_fee))
            
            if coin_after_transfer_fee <= 0:
                logger.warning(f"Transfer sonrası coin miktarı sıfır veya negatif. Coin: {self.current_coin}, Alınan Miktar: {coin_to_buy:.6f}, Transfer Ücreti: {transfer_fee:.6f}")
                return None

            # MEXC'de satış geliri (USDT cinsinden)
            sell_revenue_usdt = coin_after_transfer_fee * Decimal(str(mexc_price))
            
            profit = sell_revenue_usdt - buy_cost_usdt
            
            if buy_cost_usdt == 0: # Division by zero prevention
                profit_percentage = 0
            else:
                profit_percentage = (profit / buy_cost_usdt) * 100
            
            opportunity = {
                'gate_price': gate_price,
                'mexc_price': mexc_price,
                'transfer_fee': transfer_fee, # Bu değerin birimi önemli (coin mi, USDT mi)
                'profit': float(profit),
                'profit_percentage': float(profit_percentage),
                'is_profitable': profit_percentage >= self.min_profit_percentage
            }
            
            return opportunity
            
        except Exception as e:
            logger.error(f"Arbitraj kontrolü hatası: {e}")
            if ADMIN_CHAT_ID:
                await self.send_admin_message(f"🚨 **Hata: Arbitraj fırsatı kontrol edilirken bir sorun oluştu!**\n\nDetay: `{e}`")
            return None
    
    async def execute_arbitrage_trade(self, context: ContextTypes.DEFAULT_TYPE):
        """Arbitraj işlemini gerçekleştirir"""
        try:
            gate_price = await self.get_price_from_gate(self.current_coin)
            if not gate_price:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"❌ **İşlem başlatılamadı: Gate.io'dan {self.current_coin} fiyatı alınamadı!**",
                    parse_mode='Markdown'
                )
                return False

            # 1. Gate.io'dan coin satın al
            # Hassasiyet için Decimal kullanmak önemli
            buy_amount_usdt_decimal = Decimal(str(self.trade_amount_usdt))
            # Gate.io'da piyasa alış emri verirken, 'createMarketBuyOrderRequiresPrice: False' ayarlandığında,
            # 'amount' argümanı harcanacak USDT miktarını (quote quantity) temsil eder.
            
            # Satın alınacak coin miktarı Gate.io'nun kendisi tarafından belirlenecektir.
            # Biz sadece ne kadar USDT harcayacağımızı söylüyoruz.
            # Bu nedenle 'coin_to_buy_decimal' hesaplaması burada doğrudan kullanılmayacak.
            # buy_order'dan dönen gerçek miktarı takip edeceğiz.
            
            if buy_amount_usdt_decimal <= 0:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"❌ **İşlem başarısız: Hesaplanan alış miktarı sıfır veya negatif!**\n\nCoin: {self.current_coin}\nUSDT Miktarı: ${self.trade_amount_usdt}",
                    parse_mode='Markdown'
                )
                return False

            # Düzeltme: Gate.io'da piyasa alış emri verirken harcanacak USDT miktarını gönderiyoruz.
            buy_order = await self.gate_exchange.create_market_buy_order(
                f"{self.current_coin}/USDT", 
                float(buy_amount_usdt_decimal) # Düzeltme yapıldı: harcanacak USDT miktarı
            )
            
            logger.info(f"Gate.io alış emri: {buy_order}")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🛒 **Gate.io'da {self.current_coin} alış emri verildi.**\n\nEmir ID: `{buy_order.get('id', 'N/A')}`\nMiktar: `{buy_order.get('amount', 'N/A')}`\nFiyat: `{buy_order.get('price', 'N/A')}`",
                parse_mode='Markdown'
            )
            
            # Biraz bekle (emir gerçekleşsin)
            await asyncio.sleep(10) # Gerçekleşme süresine göre ayarlanmalı. `fetch_order` ile kontrol daha iyi

            # Emirin gerçekleştiğinden emin olmak için bakiyeyi kontrol et
            gate_balance = await self.gate_exchange.fetch_balance()
            actual_bought_coin = Decimal(str(gate_balance[self.current_coin]['free']))

            # Başlangıçta hedeflenen coin miktarı (referans için)
            # Bu, Gate.io'nun o anki satış fiyatına göre yaklaşık bir değerdir.
            estimated_coin_to_buy = buy_amount_usdt_decimal / Decimal(str(gate_price))

            if actual_bought_coin < estimated_coin_to_buy * Decimal('0.95'): # %5 sapma toleransı
                 await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"⚠️ **Gate.io alış emri tam olarak gerçekleşmemiş olabilir!**\n\nHesaplanan Yaklaşık Alış: `{estimated_coin_to_buy:.6f}`\nGerçekleşen Alış: `{actual_bought_coin:.6f}`",
                    parse_mode='Markdown'
                )
                 # Burada iptal edip yeniden deneme veya hata mesajı mantığı eklenebilir.
                 # Şimdilik devam edelim ama bu bir risk.
            
            # 2. Coin'i MEXC'ye transfer et
            # ÖNEMLİ: Gerçekte, Gate.io'dan çekilebilecek minimum ve maksimum miktarları kontrol edin.
            # Ayrıca, çekim adreslerini ve tag/memo bilgilerini doğru girdiğinizden emin olun.
            # Bu kısımlar manuel olarak yapılandırılmalıdır.
            
            # Çekim ücreti Gate.io tarafından alınır. Çekilecek miktar:
            amount_to_withdraw = actual_bought_coin * Decimal('0.99') # %1 güvenlik marjı (transfer ücretini hesaba katmak için)
                                                                    # Bu oran doğru transfer ücretine göre ayarlanmalı
            
            if amount_to_withdraw <= 0:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"❌ **İşlem başarısız: Çekilecek {self.current_coin} miktarı sıfır veya negatif!**",
                    parse_mode='Markdown'
                )
                return False

            # Wallet adresleri global değişkenlerden veya ENV'den alınmalı.
            # Placeholder adresler kullanımdan kaldırılmalı.
            if MEXC_WALLET_ADDRESS == 'YOUR_MEXC_WALLET_ADDRESS_HERE':
                 await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"❌ **İşlem başarısız: MEXC cüzdan adresi ayarlanmadı!** Lütfen kodu güncelleyin.",
                    parse_mode='Markdown'
                )
                 return False

            transfer_result = await self.gate_exchange.withdraw(
                self.current_coin,
                float(amount_to_withdraw),
                MEXC_WALLET_ADDRESS,
                tag=None # Eğer tag/memo gerekiyorsa buraya eklenmeli
            )
            
            logger.info(f"Transfer işlemi: {transfer_result}")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"📤 **{self.current_coin} transferi Gate.io'dan MEXC'ye başlatıldı.**\n\nTransfer ID: `{transfer_result.get('id', 'N/A')}`\nMiktar: `{transfer_result.get('amount', 'N/A')}`",
                parse_mode='Markdown'
            )
            
            # Transfer onayını bekle (gerçek senaryoda webhook veya sürekli durum kontrolü kullanılabilir)
            # Bu bekleme süresi, blok zinciri ağının yoğunluğuna ve transferin onay süresine bağlıdır.
            # Minimum 5-15 dakika gerçekçi olabilir, hatta daha uzun.
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"⏳ **Transferin onaylanması bekleniyor...** Yaklaşık {self.check_interval * 10} saniye (bu süre, transferin hızına göre ayarlanmalı, şu an için varsayılan bir değerdir).",
                parse_mode='Markdown'
            )
            await asyncio.sleep(self.check_interval * 10) # Örnek: Check interval'ın 10 katı bekle
            
            # 3. MEXC'de coin'i sat
            mexc_balance = await self.mexc_exchange.fetch_balance()
            coin_on_mexc = Decimal(str(mexc_balance[self.current_coin]['free']))

            if coin_on_mexc <= 0:
                 await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"❌ **MEXC'ye {self.current_coin} transferi henüz gelmedi veya miktar sıfır!** İşlem iptal ediliyor.",
                    parse_mode='Markdown'
                )
                 # Burada bir kurtarma stratejisi (örn. manuel kontrol bildirimi) eklenebilir.
                 return False

            # Satılacak miktar (MEXC'nin minimum satış miktarını kontrol edin)
            # Transfer ücreti düşüldükten sonra MEXC'ye gelen miktar üzerinden satış.
            # Buy_amount * 0.98 gibi sabit bir oran yerine, MEXC'deki gerçek bakiyeyi kullanmak daha güvenli.
            
            sell_order = await self.mexc_exchange.create_market_sell_order(
                f"{self.current_coin}/USDT",
                float(coin_on_mexc)
            )
            
            logger.info(f"MEXC satış emri: {sell_order}")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"💸 **MEXC'de {self.current_coin} satış emri verildi.**\n\nEmir ID: `{sell_order.get('id', 'N/A')}`\nMiktar: `{sell_order.get('amount', 'N/A')}`\nFiyat: `{sell_order.get('price', 'N/A')}`",
                parse_mode='Markdown'
            )
            
            # Biraz bekle (emir gerçekleşsin)
            await asyncio.sleep(10)

            # 4. USDT'yi Gate.io'ya geri gönder
            # Bu adım arbitraj döngüsünü tamamlamak için önemlidir, ancak riskli olabilir.
            # Exchange'ler arası USDT transfer ücretleri ve minimum çekim miktarları farklı olabilir.
            # Ayrıca, USDT transferleri için ağ seçimi (ERC20, TRC20, BEP20 vb.) kritiktir.
            # Bu örnekte basitleştirilmiş bir yaklaşım var. Gerçekte daha detaylı kontrol gerekli.
            
            usdt_balance_on_mexc = await self.mexc_exchange.fetch_balance()
            usdt_amount = Decimal(str(usdt_balance_on_mexc['USDT']['free']))
            
            if usdt_amount > Decimal('10'):  # Minimum 10 USDT çekim varsayımı
                if GATE_IO_WALLET_ADDRESS == 'YOUR_GATE_IO_WALLET_ADDRESS_HERE':
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"❌ **İşlem başarısız: Gate.io USDT cüzdan adresi ayarlanmadı!** Lütfen kodu güncelleyin.",
                        parse_mode='Markdown'
                    )
                    # USDT'yi MEXC'de bırakmak zorunda kalırsınız, bu da arbitraj döngüsünü bozar.
                    return False

                # USDT çekim ücretini düşerek çekilecek miktar
                # USDT transfer ücreti (genellikle sabit bir miktar veya yüzde)
                usdt_withdrawal_fee = Decimal('1.0') # Örnek USDT çekim ücreti, MEXC'den kontrol edin!
                amount_to_send_usdt = usdt_amount - usdt_withdrawal_fee

                if amount_to_send_usdt <= 0:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"⚠️ **MEXC'den çekilecek USDT miktarı transfer ücretinden düşük veya sıfır.** Transfer yapılmıyor.",
                        parse_mode='Markdown'
                    )
                else:
                    usdt_transfer = await self.mexc_exchange.withdraw(
                        'USDT',
                        float(amount_to_send_usdt),
                        GATE_IO_WALLET_ADDRESS,
                        tag=None, # Eğer tag/memo gerekiyorsa buraya eklenmeli
                        params={'network': 'TRC20'} # Ağ seçimi önemli! Örn: 'TRC20' veya 'ERC20'
                    )
                    
                    logger.info(f"USDT transfer işlemi: {usdt_transfer}")
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"🔄 **USDT transferi MEXC'den Gate.io'ya başlatıldı.**\n\nTransfer ID: `{usdt_transfer.get('id', 'N/A')}`\nMiktar: `{usdt_transfer.get('amount', 'N/A')}`",
                        parse_mode='Markdown'
                    )
            else:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"ℹ️ **MEXC'deki USDT bakiyesi çok düşük (${usdt_amount:.2f})**. USDT geri transferi yapılmadı.",
                    parse_mode='Markdown'
                )

            # İstatistikleri güncelle
            self.stats['total_trades'] += 1
            self.stats['successful_trades'] += 1
            
            # Gerçekleşen kârı hesaplamak için son bakiyeleri kontrol etmek daha doğru olur.
            # Basit bir tahminle, başlangıçta hesaplanan 'profit' değerini ekleyelim.
            # Ancak bu, emirlerin tam gerçekleştiği varsayımına dayanır.
            # Daha sağlam bir yaklaşım, işlem sonrası USDT bakiyelerindeki değişimi izlemektir.
            opportunity_after_trade = await self.check_arbitrage_opportunity() # Son fiyatlarla bir daha kontrol
            if opportunity_after_trade:
                self.stats['total_profit'] += opportunity_after_trade['profit'] # İşlem sonrası kârı ekle
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"📈 **Tahmini İşlem Kârı: ${opportunity_after_trade['profit']:.2f}**",
                    parse_mode='Markdown'
                )

            self.stats['last_trade_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            return True
            
        except ccxt.NetworkError as e:
            logger.error(f"İşlem gerçekleştirme hatası (Ağ hatası): {e}")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ **İşlem sırasında ağ hatası oluştu!**\n\nDetay: `{e}`\nLütfen internet bağlantınızı kontrol edin ve borsaların durumunu inceleyin.",
                parse_mode='Markdown'
            )
            return False
        except ccxt.ExchangeError as e:
            logger.error(f"İşlem gerçekleştirme hatası (Borsa hatası): {e}")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ **İşlem sırasında borsa hatası oluştu!**\n\nDetay: `{e}`\n(Örn: Yetersiz bakiye, geçersiz emir, API hatası)",
                parse_mode='Markdown'
            )
            return False
        except Exception as e:
            logger.error(f"İşlem gerçekleştirme hatası (Genel hata): {e}", exc_info=True) # exc_info ile traceback göster
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ **İşlem gerçekleştirme sırasında beklenmedik bir hata oluştu!**\n\nDetay: `{type(e).__name__}: {e}`",
                parse_mode='Markdown'
            )
            return False
    
    async def monitoring_loop(self, context: ContextTypes.DEFAULT_TYPE):
        """Ana izleme döngüsü"""
        while self.is_running:
            try:
                opportunity = await self.check_arbitrage_opportunity()
                
                if opportunity and opportunity['is_profitable']:
                    message = f"""
🚀 **ARBİTRAJ FIRSATI BULUNDU!**

💰 Coin: {self.current_coin}
📊 Gate.io Fiyatı: ${opportunity['gate_price']:.6f}
📊 MEXC Fiyatı: ${opportunity['mexc_price']:.6f}
💸 Transfer Ücreti: {opportunity['transfer_fee']:.6f} {self.current_coin} (tahmini)
🎯 Tahmini Kâr: ${opportunity['profit']:.2f} ({opportunity['profit_percentage']:.2f}%)

⚡ İşlem başlatılıyor...
                    """
                    
                    # Admin'e bildirim gönder
                    if ADMIN_CHAT_ID:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=message,
                            parse_mode='Markdown'
                        )
                    else:
                        logger.warning("ADMIN_CHAT_ID ayarlanmamış, arbitraj fırsatı bildirimi gönderilemedi.")

                    # İşlemi gerçekleştir
                    success = await self.execute_arbitrage_trade(context) # context'i buraya ekledik
                    
                    if ADMIN_CHAT_ID:
                        if success:
                            await context.bot.send_message(
                                chat_id=ADMIN_CHAT_ID,
                                text="✅ **İşlem başarıyla tamamlandı!**",
                                parse_mode='Markdown'
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=ADMIN_CHAT_ID,
                                text="❌ **İşlem başarısız oldu! Detaylar için yukarıdaki hataları kontrol edin.**",
                                parse_mode='Markdown'
                            )
                else:
                    if opportunity:
                        logger.info(f"Kârlı fırsat yok. {self.current_coin} - Kâr: {opportunity['profit_percentage']:.2f}% (Min: {self.min_profit_percentage}%)")
                    else:
                        logger.warning(f"Arbitraj fırsatı kontrolü başarısız oldu veya veri alınamadı. {self.current_coin}")
                
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"İzleme döngüsü hatası: {e}", exc_info=True)
                if ADMIN_CHAT_ID:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"🚨 **İzleme döngüsünde kritik hata!**\n\nDetay: `{type(e).__name__}: {e}`\nBot durdurulmuş olabilir veya stabil çalışmıyor.",
                        parse_mode='Markdown'
                    )
                # Hata durumunda botun tamamen durmasını engellemek için daha uzun bekleyebiliriz.
                await asyncio.sleep(60)

# Telegram Bot Komutları
arbitrage_bot = None # Bu global değişken main fonksiyonunda atanacak

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot başlatma komutu"""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Botu Başlat", callback_data='start_bot'),
            InlineKeyboardButton("⏹️ Botu Durdur", callback_data='stop_bot')
        ],
        [
            InlineKeyboardButton("⚙️ Ayarlar", callback_data='settings'),
            InlineKeyboardButton("📊 İstatistikler", callback_data='stats')
        ],
        [
            InlineKeyboardButton("💰 Coin Değiştir", callback_data='change_coin'),
            InlineKeyboardButton("🔍 Fiyat Kontrol", callback_data='check_prices')
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 **Arbitraj Botu'na Hoş Geldiniz!**\n\n"
        "Bu bot Gate.io ve MEXC borsaları arasında arbitraj fırsatlarını otomatik olarak değerlendirir.\n\n"
        "Lütfen bir seçenek seçin:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buton callback handler"""
    global arbitrage_bot
    query = update.callback_query
    await query.answer() # Butona basıldığında bildirim gönderir
    
    if query.data == 'start_bot':
        if not arbitrage_bot:
            await query.edit_message_text("❌ **Bot henüz başlatılmadı!** `/start` komutunu kullanarak botu başlatın.", parse_mode='Markdown')
            return

        if not arbitrage_bot.is_running:
            arbitrage_bot.is_running = True
            # Monitoring loop'u başlat
            context.application.create_task(arbitrage_bot.monitoring_loop(context))
            await query.edit_message_text("✅ **Bot başlatıldı ve arbitraj fırsatları izleniyor...**", parse_mode='Markdown')
        else:
            await query.edit_message_text("⚠️ **Bot zaten çalışıyor!**", parse_mode='Markdown')
    
    elif query.data == 'stop_bot':
        if not arbitrage_bot:
            await query.edit_message_text("❌ **Bot henüz başlatılmadı!**", parse_mode='Markdown')
            return
        arbitrage_bot.is_running = False
        await query.edit_message_text("⏹️ **Bot durduruldu.**", parse_mode='Markdown')
    
    elif query.data == 'settings':
        if not arbitrage_bot:
            await query.edit_message_text("❌ **Bot henüz başlatılmadı!**", parse_mode='Markdown')
            return
        settings_text = f"""
⚙️ **Mevcut Ayarlar:**

💰 İşlem Miktarı: ${arbitrage_bot.trade_amount_usdt} USDT
🎯 Minimum Kâr Oranı: %{arbitrage_bot.min_profit_percentage}
⏱️ Kontrol Aralığı: {arbitrage_bot.check_interval} saniye
🪙 Aktif Coin: {arbitrage_bot.current_coin}

Ayarları değiştirmek için ilgili komutu kullanın:
/set_amount <miktar>
/set_profit <oran>
/set_interval <saniye>
        """
        await query.edit_message_text(settings_text, parse_mode='Markdown')
    
    elif query.data == 'stats':
        if not arbitrage_bot:
            await query.edit_message_text("❌ **Bot henüz başlatılmadı!**", parse_mode='Markdown')
            return
        stats_text = f"""
📊 **Bot İstatistikleri:**

📈 Toplam İşlem: {arbitrage_bot.stats['total_trades']}
✅ Başarılı İşlem: {arbitrage_bot.stats['successful_trades']}
💰 Toplam Kâr: ${arbitrage_bot.stats['total_profit']:.2f}
🕐 Son İşlem: {arbitrage_bot.stats['last_trade_time'] or 'Henüz işlem yok'}
        """
        await query.edit_message_text(stats_text, parse_mode='Markdown')
    
    elif query.data == 'change_coin':
        if not arbitrage_bot:
            await query.edit_message_text("❌ **Bot henüz başlatılmadı!**", parse_mode='Markdown')
            return
        await query.edit_message_text(
            "💰 **Coin değiştirmek için aşağıdaki formatı kullanın:**\n\n"
            "`/coin <COIN_SYMBOL>`\n\n"
            "Örnek: `/coin BTC` veya `/coin ETH`",
            parse_mode='Markdown'
        )
    
    elif query.data == 'check_prices':
        if not arbitrage_bot:
            await query.edit_message_text("❌ **Bot henüz başlatılmadı!**", parse_mode='Markdown')
            return
        try:
            opportunity = await arbitrage_bot.check_arbitrage_opportunity()
            if opportunity:
                price_text = f"""
🔍 **Anlık Fiyat Bilgileri:**

💰 Coin: {arbitrage_bot.current_coin}
📊 Gate.io: ${opportunity['gate_price']:.6f}
📊 MEXC: ${opportunity['mexc_price']:.6f}
💸 Transfer Ücreti: {opportunity['transfer_fee']:.6f} {arbitrage_bot.current_coin} (tahmini)
🎯 Potansiyel Kâr: ${opportunity['profit']:.2f} ({opportunity['profit_percentage']:.2f}%)

{'✅ KÂRLI!' if opportunity['is_profitable'] else '❌ Kârlı değil'}
                """
            else:
                price_text = "❌ **Fiyat bilgileri alınamadı! Lütfen logları veya admin kanalını kontrol edin.**"
            
            await query.edit_message_text(price_text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Fiyat kontrolü callback hatası: {e}", exc_info=True)
            await query.edit_message_text(f"❌ **Hata:** Fiyat kontrolü sırasında bir sorun oluştu. Detay: `{e}`", parse_mode='Markdown')

async def set_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coin değiştirme komutu"""
    global arbitrage_bot
    if not arbitrage_bot:
        await update.message.reply_text("❌ **Bot henüz başlatılmadı!** `/start` komutunu kullanarak botu başlatın.", parse_mode='Markdown')
        return

    if context.args:
        new_coin = context.args[0].upper()
        # Coin sembolünün geçerliliğini basitçe kontrol et
        if len(new_coin) < 2 or not new_coin.isalnum():
            await update.message.reply_text("❌ **Geçersiz coin sembolü!** Lütfen alfabetik ve en az 2 karakterli bir sembol girin.", parse_mode='Markdown')
            return

        arbitrage_bot.current_coin = new_coin
        await update.message.reply_text(f"✅ **Aktif coin {new_coin} olarak değiştirildi!**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ **Kullanım:** `/coin <COIN_SYMBOL>`", parse_mode='Markdown')

async def set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """İşlem miktarı ayarlama"""
    global arbitrage_bot
    if not arbitrage_bot:
        await update.message.reply_text("❌ **Bot henüz başlatılmadı!** `/start` komutunu kullanarak botu başlatın.", parse_mode='Markdown')
        return

    if context.args:
        try:
            amount = float(context.args[0])
            if amount <= 0:
                await update.message.reply_text("❌ **İşlem miktarı pozitif bir sayı olmalıdır!**", parse_mode='Markdown')
                return
            arbitrage_bot.trade_amount_usdt = amount
            await update.message.reply_text(f"✅ **İşlem miktarı ${amount} USDT olarak ayarlandı!**", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ **Geçerli bir sayı girin!**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ **Kullanım:** `/set_amount <miktar>`", parse_mode='Markdown')

async def set_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Minimum kâr oranı ayarlama"""
    global arbitrage_bot
    if not arbitrage_bot:
        await update.message.reply_text("❌ **Bot henüz başlatılmadı!** `/start` komutunu kullanarak botu başlatın.", parse_mode='Markdown')
        return

    if context.args:
        try:
            profit = float(context.args[0])
            if profit < 0:
                await update.message.reply_text("❌ **Kâr oranı negatif olamaz!**", parse_mode='Markdown')
                return
            arbitrage_bot.min_profit_percentage = profit
            await update.message.reply_text(f"✅ **Minimum kâr oranı %{profit} olarak ayarlandı!**", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ **Geçerli bir sayı girin!**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ **Kullanım:** `/set_profit <oran>`", parse_mode='Markdown')

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kontrol aralığı ayarlama"""
    global arbitrage_bot
    if not arbitrage_bot:
        await update.message.reply_text("❌ **Bot henüz başlatılmadı!** `/start` komutunu kullanarak botu başlatın.", parse_mode='Markdown')
        return

    if context.args:
        try:
            interval = int(context.args[0])
            if interval < 10:
                await update.message.reply_text("❌ **Minimum aralık 10 saniye olmalıdır!**", parse_mode='Markdown')
                return
            arbitrage_bot.check_interval = interval
            await update.message.reply_text(f"✅ **Kontrol aralığı {interval} saniye olarak ayarlandı!**", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ **Geçerli bir sayı girin!**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ **Kullanım:** `/set_interval <saniye>`", parse_mode='Markdown')

async def initialize_bot_instance():
    """Bot'u başlatır ve global değişkene atar"""
    global arbitrage_bot
    
    # Environment variables'dan konfigürasyon al
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    GATE_API_KEY = os.getenv('GATE_API_KEY')
    GATE_SECRET = os.getenv('GATE_SECRET')
    MEXC_API_KEY = os.getenv('MEXC_API_KEY')
    MEXC_SECRET = os.getenv('MEXC_SECRET')
    
    # Gerekli environment variables kontrolü
    required_vars = {
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'GATE_API_KEY': GATE_API_KEY,
        'GATE_SECRET': GATE_SECRET,
        'MEXC_API_KEY': MEXC_API_KEY,
        'MEXC_SECRET': MEXC_SECRET,
        'ADMIN_CHAT_ID': ADMIN_CHAT_ID, # Admin chat ID'si de önemli
    }
    
    missing_vars = [var_name for var_name, value in required_vars.items() if not value]
    if missing_vars:
        error_msg = f"Gerekli environment variables eksik: {', '.join(missing_vars)}"
        logger.critical(error_msg)
        raise Exception(error_msg + "\nLütfen Railway veya ortam değişkenlerinizi kontrol edin.")

    # Arbitrage bot'u başlat
    arbitrage_bot = ArbitrageBot(
        TELEGRAM_TOKEN, GATE_API_KEY, GATE_SECRET, 
        MEXC_API_KEY, MEXC_SECRET
    )
    
    # Exchange bağlantılarını başlat
    if not await arbitrage_bot.initialize_exchanges():
        raise Exception("Exchange bağlantıları kurulamadı. Bot başlatılamadı.")
    
    return arbitrage_bot

async def main():
    """Ana fonksiyon - Railway için async"""
    try:
        # Bot'u başlat
        await initialize_bot_instance() # initialize_bot_instance çağrılıyor
        
        # Telegram application'ı kur
        application = Application.builder().token(os.getenv('TELEGRAM_TOKEN')).build()
        
        # Handler'ları ekle
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_handler(CommandHandler("coin", set_coin))
        application.add_handler(CommandHandler("set_amount", set_amount))
        application.add_handler(CommandHandler("set_profit", set_profit))
        application.add_handler(CommandHandler("set_interval", set_interval)) 
        
        # Bot'u başlat
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        logger.info("🚀 Arbitraj botu Railway üzerinde başlatıldı!")
        
        # Sonsuz döngü (Railway'de çalışmaya devam etmek için)
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Bot durduruluyor...")
        finally:
            if application.running: # Sadece çalışıyorsa durdur
                await application.stop()
            
    except Exception as e:
        logger.error(f"Bot başlatma hatası: {e}")
        # Başlangıçta ADMIN_CHAT_ID belirlenememişse telegram üzerinden bildirim gönderemeyiz.
        # Bu durumda sadece loglara yazarız.
        # Eğer ADMIN_CHAT_ID ayarlıysa, manuel olarak telegrama mesaj gönderebiliriz.
        if ADMIN_CHAT_ID:
            try:
                # Bot başlatılamadığı için application objesi henüz oluşmamış olabilir.
                # Bu yüzden doğrudan telegram-bot API kullanarak mesaj göndermeyi deneyelim.
                # Bu kısım manuel müdahale gerektirebilir veya daha robust bir başlangıç hatası bildirimi mekanizması.
                # Örnek: `requests` veya `httpx` ile doğrudan Telegram API'ye POST yapmak.
                logger.critical(f"Kritik hata! Telegram botu başlatılamadı. Lütfen sunucu loglarını kontrol edin. Hata: {e}")
            except Exception as inner_e:
                logger.critical(f"Kritik hata bildirimi gönderilirken hata oluştu: {inner_e}")
        
        raise # Hatanın Railway tarafından görülmesi için yeniden fırlat

if __name__ == '__main__':
    asyncio.run(main())
