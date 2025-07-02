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
            })
            
            self.mexc_exchange = ccxt.mexc({
                'apiKey': self.mexc_api_key,
                'secret': self.mexc_secret,
                'sandbox': False,
                'enableRateLimit': True,
            })
            
            # Bağlantıları test et
            await self.gate_exchange.load_markets()
            await self.mexc_exchange.load_markets()
            
            logger.info("Exchange bağlantıları başarıyla kuruldu")
            return True
            
        except Exception as e:
            logger.error(f"Exchange bağlantısında hata: {e}")
            return False
    
    async def get_price_from_gate(self, symbol):
        """Gate.io'dan fiyat bilgisi alır"""
        try:
            ticker = await self.gate_exchange.fetch_ticker(f"{symbol}/USDT")
            return ticker['bid']  # Alış fiyatı
        except Exception as e:
            logger.error(f"Gate.io fiyat alma hatası: {e}")
            return None
    
    async def get_price_from_mexc(self, symbol):
        """MEXC'den fiyat bilgisi alır"""
        try:
            ticker = await self.mexc_exchange.fetch_ticker(f"{symbol}/USDT")
            return ticker['ask']  # Satış fiyatı
        except Exception as e:
            logger.error(f"MEXC fiyat alma hatası: {e}")
            return None
    
    async def get_transfer_fee(self, symbol):
        """Transfer ücreti hesaplar (yaklaşık)"""
        # Bu değerler gerçek API'den alınmalı, şimdilik sabit değerler
        transfer_fees = {
            'WHITE': 0.1,  # Örnek değer
            'BTC': 0.0005,
            'ETH': 0.01,
            'BNB': 0.01
        }
        return transfer_fees.get(symbol, 0.1)
    
    async def check_arbitrage_opportunity(self):
        """Arbitraj fırsatı kontrol eder"""
        try:
            gate_price = await self.get_price_from_gate(self.current_coin)
            mexc_price = await self.get_price_from_mexc(self.current_coin)
            
            if not gate_price or not mexc_price:
                return None
            
            transfer_fee = await self.get_transfer_fee(self.current_coin)
            
            # Kâr hesaplama
            buy_cost = gate_price * (self.trade_amount_usdt / gate_price)
            sell_revenue = mexc_price * (self.trade_amount_usdt / gate_price)
            transfer_cost = transfer_fee * mexc_price
            
            profit = sell_revenue - buy_cost - transfer_cost
            profit_percentage = (profit / buy_cost) * 100
            
            opportunity = {
                'gate_price': gate_price,
                'mexc_price': mexc_price,
                'transfer_fee': transfer_fee,
                'profit': profit,
                'profit_percentage': profit_percentage,
                'is_profitable': profit_percentage >= self.min_profit_percentage
            }
            
            return opportunity
            
        except Exception as e:
            logger.error(f"Arbitraj kontrolü hatası: {e}")
            return None
    
    async def execute_arbitrage_trade(self):
        """Arbitraj işlemini gerçekleştirir"""
        try:
            # 1. Gate.io'dan coin satın al
            buy_amount = self.trade_amount_usdt / await self.get_price_from_gate(self.current_coin)
            buy_order = await self.gate_exchange.create_market_buy_order(
                f"{self.current_coin}/USDT", 
                buy_amount
            )
            
            logger.info(f"Gate.io alış emri: {buy_order}")
            
            # Biraz bekle (emir gerçekleşsin)
            await asyncio.sleep(5)
            
            # 2. Coin'i MEXC'ye transfer et
            transfer_result = await self.gate_exchange.withdraw(
                self.current_coin,
                buy_amount * 0.99,  # %1 güvenlik marjı
                "MEXC_WALLET_ADDRESS",  # Gerçek adres gerekli
                tag=None
            )
            
            logger.info(f"Transfer işlemi: {transfer_result}")
            
            # Transfer onayını bekle (gerçek senaryoda webhook kullanılabilir)
            await asyncio.sleep(300)  # 5 dakika bekle
            
            # 3. MEXC'de coin'i sat
            sell_order = await self.mexc_exchange.create_market_sell_order(
                f"{self.current_coin}/USDT",
                buy_amount * 0.98  # Transfer ücreti düşüldükten sonra
            )
            
            logger.info(f"MEXC satış emri: {sell_order}")
            
            # 4. USDT'yi Gate.io'ya geri gönder
            await asyncio.sleep(5)
            usdt_balance = await self.mexc_exchange.fetch_balance()
            usdt_amount = usdt_balance['USDT']['free']
            
            if usdt_amount > 10:  # Minimum 10 USDT
                usdt_transfer = await self.mexc_exchange.withdraw(
                    'USDT',
                    usdt_amount * 0.99,
                    "GATE_IO_WALLET_ADDRESS",  # Gerçek adres gerekli
                    tag=None
                )
                
                logger.info(f"USDT transfer işlemi: {usdt_transfer}")
            
            # İstatistikleri güncelle
            self.stats['total_trades'] += 1
            self.stats['successful_trades'] += 1
            self.stats['last_trade_time'] = datetime.now()
            
            return True
            
        except Exception as e:
            logger.error(f"İşlem gerçekleştirme hatası: {e}")
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
💸 Transfer Ücreti: ${opportunity['transfer_fee']:.6f}
🎯 Tahmini Kâr: ${opportunity['profit']:.2f} ({opportunity['profit_percentage']:.2f}%)

⚡ İşlem başlatılıyor...
                    """
                    
                    # Admin'e bildirim gönder
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,  # Admin chat ID'si
                        text=message,
                        parse_mode='Markdown'
                    )
                    
                    # İşlemi gerçekleştir
                    success = await self.execute_arbitrage_trade()
                    
                    if success:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text="✅ **İşlem başarıyla tamamlandı!**",
                            parse_mode='Markdown'
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text="❌ **İşlem başarısız oldu!**",
                            parse_mode='Markdown'
                        )
                
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"İzleme döngüsü hatası: {e}")
                await asyncio.sleep(60)

# Telegram Bot Komutları
arbitrage_bot = None

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
    await query.answer()
    
    if query.data == 'start_bot':
        if not arbitrage_bot.is_running:
            arbitrage_bot.is_running = True
            # Monitoring loop'u başlat
            context.application.create_task(arbitrage_bot.monitoring_loop(context))
            await query.edit_message_text("✅ **Bot başlatıldı ve arbitraj fırsatları izleniyor...**", parse_mode='Markdown')
        else:
            await query.edit_message_text("⚠️ **Bot zaten çalışıyor!**", parse_mode='Markdown')
    
    elif query.data == 'stop_bot':
        arbitrage_bot.is_running = False
        await query.edit_message_text("⏹️ **Bot durduruldu.**", parse_mode='Markdown')
    
    elif query.data == 'settings':
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
        stats_text = f"""
📊 **Bot İstatistikleri:**

📈 Toplam İşlem: {arbitrage_bot.stats['total_trades']}
✅ Başarılı İşlem: {arbitrage_bot.stats['successful_trades']}
💰 Toplam Kâr: ${arbitrage_bot.stats['total_profit']:.2f}
🕐 Son İşlem: {arbitrage_bot.stats['last_trade_time'] or 'Henüz işlem yok'}
        """
        await query.edit_message_text(stats_text, parse_mode='Markdown')
    
    elif query.data == 'change_coin':
        await query.edit_message_text(
            "💰 **Coin değiştirmek için aşağıdaki formatı kullanın:**\n\n"
            "`/coin <COIN_SYMBOL>`\n\n"
            "Örnek: `/coin BTC` veya `/coin ETH`",
            parse_mode='Markdown'
        )
    
    elif query.data == 'check_prices':
        try:
            opportunity = await arbitrage_bot.check_arbitrage_opportunity()
            if opportunity:
                price_text = f"""
🔍 **Anlık Fiyat Bilgileri:**

💰 Coin: {arbitrage_bot.current_coin}
📊 Gate.io: ${opportunity['gate_price']:.6f}
📊 MEXC: ${opportunity['mexc_price']:.6f}
💸 Transfer Ücreti: ${opportunity['transfer_fee']:.6f}
🎯 Potansiyel Kâr: ${opportunity['profit']:.2f} ({opportunity['profit_percentage']:.2f}%)

{'✅ KÂRLİ!' if opportunity['is_profitable'] else '❌ Kârlı değil'}
                """
            else:
                price_text = "❌ **Fiyat bilgileri alınamadı!**"
            
            await query.edit_message_text(price_text, parse_mode='Markdown')
        except Exception as e:
            await query.edit_message_text(f"❌ **Hata:** {str(e)}", parse_mode='Markdown')

async def set_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coin değiştirme komutu"""
    if context.args:
        new_coin = context.args[0].upper()
        arbitrage_bot.current_coin = new_coin
        await update.message.reply_text(f"✅ **Aktif coin {new_coin} olarak değiştirildi!**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ **Kullanım:** `/coin <COIN_SYMBOL>`", parse_mode='Markdown')

async def set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """İşlem miktarı ayarlama"""
    if context.args:
        try:
            amount = float(context.args[0])
            arbitrage_bot.trade_amount_usdt = amount
            await update.message.reply_text(f"✅ **İşlem miktarı ${amount} USDT olarak ayarlandı!**", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ **Geçerli bir sayı girin!**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ **Kullanım:** `/set_amount <miktar>`", parse_mode='Markdown')

async def set_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Minimum kâr oranı ayarlama"""
    if context.args:
        try:
            profit = float(context.args[0])
            arbitrage_bot.min_profit_percentage = profit
            await update.message.reply_text(f"✅ **Minimum kâr oranı %{profit} olarak ayarlandı!**", parse_mode='Markdown')
        except ValueError:
            await update.message.reply_text("❌ **Geçerli bir sayı girin!**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ **Kullanım:** `/set_profit <oran>`", parse_mode='Markdown')

# Global değişkenler
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')  # Admin chat ID'si

async def initialize_bot():
    """Bot'u başlatır"""
    global arbitrage_bot
    
    # Environment variables'dan konfigürasyon al
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    GATE_API_KEY = os.getenv('GATE_API_KEY')
    GATE_SECRET = os.getenv('GATE_SECRET')
    MEXC_API_KEY = os.getenv('MEXC_API_KEY')
    MEXC_SECRET = os.getenv('MEXC_SECRET')
    
    # Gerekli environment variables kontrolü
    required_vars = [TELEGRAM_TOKEN, GATE_API_KEY, GATE_SECRET, MEXC_API_KEY, MEXC_SECRET]
    if not all(required_vars):
        raise Exception("Gerekli environment variables eksik!")
    
    # Arbitrage bot'u başlat
    arbitrage_bot = ArbitrageBot(
        TELEGRAM_TOKEN, GATE_API_KEY, GATE_SECRET, 
        MEXC_API_KEY, MEXC_SECRET
    )
    
    # Exchange bağlantılarını başlat
    await arbitrage_bot.initialize_exchanges()
    
    return arbitrage_bot

async def main():
    """Ana fonksiyon - Railway için async"""
    try:
        # Bot'u başlat
        await initialize_bot()
        
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
            await application.stop()
            
    except Exception as e:
        logger.error(f"Bot başlatma hatası: {e}")
        raise

async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kontrol aralığı ayarlama"""
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

if __name__ == '__main__':
    asyncio.run(main())
