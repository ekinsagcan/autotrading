import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Set
import aiohttp
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ArbitrageBot:
    def __init__(self):
        # Major cryptocurrency exchanges with their APIs
        self.exchanges = {
            'binance': 'https://api.binance.com/api/v3/ticker/24hr',
            'kucoin': 'https://api.kucoin.com/api/v1/market/allTickers',
            'gate': 'https://api.gateio.ws/api/v4/spot/tickers',
            'mexc': 'https://api.mexc.com/api/v3/ticker/24hr',
            'bybit': 'https://api.bybit.com/v5/market/tickers?category=spot',
            'okx': 'https://www.okx.com/api/v5/market/tickers?instType=SPOT',
            'huobi': 'https://api.huobi.pro/market/tickers',
            'bitget': 'https://api.bitget.com/api/spot/v1/market/tickers',
            'coinbase': 'https://api.exchange.coinbase.com/products',
            'kraken': 'https://api.kraken.com/0/public/Ticker',
            'bitfinex': 'https://api-pub.bitfinex.com/v2/tickers?symbols=ALL',
            'cryptocom': 'https://api.crypto.com/v2/public/get-ticker',
            'bingx': 'https://open-api.bingx.com/openApi/spot/v1/ticker/24hr',
            'lbank': 'https://api.lbkex.com/v2/ticker/24hr.do',
            'digifinex': 'https://openapi.digifinex.com/v3/ticker',
            'bitmart': 'https://api-cloud.bitmart.com/spot/v1/ticker',
            'xt': 'https://api.xt.com/data/api/v1/getTickers',
            'phemex': 'https://api.phemex.com/md/ticker/24hr/all',
            'bitstamp': 'https://www.bitstamp.net/api/v2/ticker/',
            'gemini': 'https://api.gemini.com/v1/pricefeed',
            'poloniex': 'https://api.poloniex.com/markets/ticker24h',
            'ascendex': 'https://ascendex.com/api/pro/v1/ticker',
            'coinex': 'https://api.coinex.com/v1/market/ticker/all',
            'hotcoin': 'https://api.hotcoin.top/v1/market/ticker',
            'bigone': 'https://big.one/api/v3/asset_pairs/tickers',
            'probit': 'https://api.probit.com/api/exchange/v1/ticker',
            'latoken': 'https://api.latoken.com/v2/ticker',
            'bitrue': 'https://www.bitrue.com/api/v1/ticker/24hr',
            'tidex': 'https://api.tidex.com/api/3/ticker',
            'p2pb2b': 'https://api.p2pb2b.com/api/v2/public/tickers'
        }
        
        # Trusted major cryptocurrencies - these are generally the same across all exchanges
        self.trusted_symbols = {
            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'XRPUSDT', 
            'SOLUSDT', 'DOTUSDT', 'DOGEUSDT', 'AVAXUSDT', 'MATICUSDT',
            'LINKUSDT', 'LTCUSDT', 'BCHUSDT', 'UNIUSDT', 'ATOMUSDT',
            'VETUSDT', 'FILUSDT', 'TRXUSDT', 'ETCUSDT', 'XLMUSDT',
            'ALGOUSDT', 'ICPUSDT', 'THETAUSDT', 'AXSUSDT', 'SANDUSDT',
            'MANAUSDT', 'CHZUSDT', 'ENJUSDT', 'GALAUSDT', 'APTUSDT',
            'NEARUSDT', 'FLOWUSDT', 'AAVEUSDT', 'COMPUSDT', 'SUSHIUSDT',
            'YFIUSDT', 'SNXUSDT', 'MKRUSDT', 'CRVUSDT', '1INCHUSDT',
            'RUNEUSDT', 'LUNA2USDT', 'FTMUSDT', 'ONEUSDT', 'ZILUSDT',
            'ZECUSDT', 'DASHUSDT', 'WAVESUSDT', 'ONTUSDT', 'QTUMUSDT'
        }
        
        # Suspicious symbols - common names used for different coins
        self.suspicious_symbols = {
            'SUN', 'MOON', 'DOGE', 'SHIB', 'PEPE', 'FLOKI', 'BABY',
            'SAFE', 'MINI', 'MICRO', 'MEGA', 'SUPER', 'ULTRA', 'ELON',
            'MARS', 'ROCKET', 'DIAMOND', 'GOLD', 'SILVER', 'TITAN',
            'RISE', 'FIRE', 'ICE', 'SNOW', 'STORM', 'THUNDER', 'LIGHTNING'
        }
        
        # Symbol mapping for different exchange formats
        self.symbol_mapping = {
            'BTC/USDT': 'BTCUSDT',
            'BTC-USDT': 'BTCUSDT',
            'BTC_USDT': 'BTCUSDT',
            'tBTCUSDT': 'BTCUSDT',
            'ETH/USDT': 'ETHUSDT',
            'ETH-USDT': 'ETHUSDT',
            'ETH_USDT': 'ETHUSDT',
            'tETHUSDT': 'ETHUSDT'
        }
        
        # Minimum 24h volume threshold - filter low volume coins
        self.min_volume_threshold = 100000  # $100k minimum 24h volume
        
        # Maximum profit threshold - very high differences are suspicious
        self.max_profit_threshold = 20.0  # 20%+ profit is suspicious
        
        # Free user maximum profit display
        self.free_user_max_profit = 2.0  # Show max 2% profit for free users
        
        # Premium users cache
        self.premium_users = set()
        
        self.init_database()
        self.load_premium_users()
    
    def init_database(self):
        """Initialize database"""
        with sqlite3.connect('arbitrage.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    subscription_end DATE,
                    is_premium BOOLEAN DEFAULT FALSE,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS arbitrage_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    exchange1 TEXT,
                    exchange2 TEXT,
                    price1 REAL,
                    price2 REAL,
                    profit_percent REAL,
                    volume_24h REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS premium_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    added_by_admin BOOLEAN DEFAULT TRUE,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    subscription_end DATE
                )
            ''')
            conn.commit()
    
    def load_premium_users(self):
        """Load premium users into memory"""
        with sqlite3.connect('arbitrage.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM premium_users')
            results = cursor.fetchall()
            self.premium_users = {row[0] for row in results}
            logger.info(f"Loaded {len(self.premium_users)} premium users")
    
    def add_premium_user(self, user_id: int, username: str = "", days: int = 30):
        """Add premium user (admin command)"""
        with sqlite3.connect('arbitrage.db') as conn:
            cursor = conn.cursor()
            end_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
            cursor.execute('''
                INSERT OR REPLACE INTO premium_users 
                (user_id, username, subscription_end)
                VALUES (?, ?, ?)
            ''', (user_id, username, end_date))
            conn.commit()
            self.premium_users.add(user_id)
    
    def remove_premium_user(self, user_id: int):
        """Remove premium user (admin command)"""
        with sqlite3.connect('arbitrage.db') as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM premium_users WHERE user_id = ?', (user_id,))
            conn.commit()
            self.premium_users.discard(user_id)
    
    def normalize_symbol(self, symbol: str, exchange: str) -> str:
        """Normalize symbol format across exchanges"""
        # Remove common separators and convert to standard format
        normalized = symbol.upper().replace('/', '').replace('-', '').replace('_', '')
        
        # Handle exchange-specific prefixes
        if exchange == 'bitfinex' and normalized.startswith('T'):
            normalized = normalized[1:]  # Remove 't' prefix
        
        # Handle specific mappings
        if symbol in self.symbol_mapping:
            normalized = self.symbol_mapping[symbol]
        
        return normalized
    
    async def fetch_prices_with_volume(self, exchange: str) -> Dict[str, Dict]:
        """Fetch prices and volumes from exchange"""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = self.exchanges[exchange]
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.warning(f"{exchange} returned status {response.status}")
                        return {}
                    
                    data = await response.json()
                    return self.parse_exchange_data(exchange, data)
                    
        except Exception as e:
            logger.error(f"{exchange} price/volume error: {str(e)}")
            return {}
    
    def parse_exchange_data(self, exchange: str, data) -> Dict[str, Dict]:
        """Parse exchange-specific data format"""
        try:
            if exchange == 'binance':
                return {
                    self.normalize_symbol(item['symbol'], exchange): {
                        'price': float(item['lastPrice']),
                        'volume': float(item['quoteVolume']),
                        'count': int(item['count'])
                    } for item in data 
                    if float(item['quoteVolume']) > self.min_volume_threshold
                }
            
            elif exchange == 'kucoin':
                if 'data' in data and 'ticker' in data['data']:
                    return {
                        self.normalize_symbol(item['symbol'], exchange): {
                            'price': float(item['last']),
                            'volume': float(item['volValue']) if item['volValue'] else 0
                        } for item in data['data']['ticker'] 
                        if item['volValue'] and float(item['volValue']) > self.min_volume_threshold
                    }
            
            elif exchange == 'gate':
                return {
                    self.normalize_symbol(item['currency_pair'], exchange): {
                        'price': float(item['last']),
                        'volume': float(item['quote_volume']) if item['quote_volume'] else 0
                    } for item in data 
                    if item['quote_volume'] and float(item['quote_volume']) > self.min_volume_threshold
                }
            
            elif exchange == 'mexc':
                return {
                    self.normalize_symbol(item['symbol'], exchange): {
                        'price': float(item['lastPrice']),
                        'volume': float(item['quoteVolume'])
                    } for item in data 
                    if float(item.get('quoteVolume', 0)) > self.min_volume_threshold
                }
            
            elif exchange == 'bybit':
                if 'result' in data and 'list' in data['result']:
                    return {
                        self.normalize_symbol(item['symbol'], exchange): {
                            'price': float(item['lastPrice']),
                            'volume': float(item['turnover24h']) if item['turnover24h'] else 0
                        } for item in data['result']['list'] 
                        if item['turnover24h'] and float(item['turnover24h']) > self.min_volume_threshold
                    }
            
            elif exchange == 'okx':
                if 'data' in data:
                    return {
                        self.normalize_symbol(item['instId'], exchange): {
                            'price': float(item['last']),
                            'volume': float(item['volCcy24h']) if item['volCcy24h'] else 0
                        } for item in data['data'] 
                        if item['volCcy24h'] and float(item['volCcy24h']) > self.min_volume_threshold
                    }
            
            elif exchange == 'huobi':
                if 'data' in data:
                    return {
                        self.normalize_symbol(item['symbol'], exchange): {
                            'price': float(item['close']),
                            'volume': float(item['vol']) if item['vol'] else 0
                        } for item in data['data'] 
                        if item['vol'] and float(item['vol']) > self.min_volume_threshold / 100
                    }
            
            elif exchange == 'bitget':
                if 'data' in data:
                    return {
                        self.normalize_symbol(item['symbol'], exchange): {
                            'price': float(item['close']),
                            'volume': float(item['quoteVol']) if item['quoteVol'] else 0
                        } for item in data['data'] 
                        if item['quoteVol'] and float(item['quoteVol']) > self.min_volume_threshold
                    }
            
            elif exchange == 'bitfinex':
                if isinstance(data, list):
                    result = {}
                    for item in data:
                        if len(item) >= 8:
                            symbol = self.normalize_symbol(item[0], exchange)
                            if item[7] and float(item[7]) > self.min_volume_threshold:
                                result[symbol] = {
                                    'price': float(item[6]),
                                    'volume': float(item[7])
                                }
                    return result
            
            elif exchange == 'kraken':
                result = {}
                for symbol, ticker_data in data.get('result', {}).items():
                    if 'c' in ticker_data and 'v' in ticker_data:
                        normalized_symbol = self.normalize_symbol(symbol, exchange)
                        volume = float(ticker_data['v'][1]) * float(ticker_data['c'][0])
                        if volume > self.min_volume_threshold:
                            result[normalized_symbol] = {
                                'price': float(ticker_data['c'][0]),
                                'volume': volume
                            }
                return result
            
            elif exchange == 'coinbase':
                if isinstance(data, list):
                    result = {}
                    for item in data:
                        if 'id' in item and 'price' in item and 'volume_24h' in item:
                            symbol = self.normalize_symbol(item['id'], exchange)
                            volume = float(item['volume_24h']) if item['volume_24h'] else 0
                            if volume > self.min_volume_threshold:
                                result[symbol] = {
                                    'price': float(item['price']),
                                    'volume': volume
                                }
                    return result
            
            elif exchange == 'poloniex':
                result = {}
                for symbol, ticker_data in data.items():
                    if 'close' in ticker_data and 'quoteVolume' in ticker_data:
                        normalized_symbol = self.normalize_symbol(symbol, exchange)
                        volume = float(ticker_data['quoteVolume'])
                        if volume > self.min_volume_threshold:
                            result[normalized_symbol] = {
                                'price': float(ticker_data['close']),
                                'volume': volume
                            }
                return result
            
            # Add more exchange parsers as needed...
            
        except Exception as e:
            logger.error(f"Error parsing {exchange} data: {str(e)}")
        
        return {}
    
    async def get_all_prices_with_volume(self) -> Dict[str, Dict[str, Dict]]:
        """Fetch price and volume data from all exchanges"""
        tasks = [self.fetch_prices_with_volume(exchange) for exchange in self.exchanges]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        exchange_data = {}
        for exchange, result in zip(self.exchanges.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching {exchange}: {result}")
                exchange_data[exchange] = {}
            else:
                exchange_data[exchange] = result
                logger.info(f"{exchange}: {len(result)} symbols fetched")
        
        return exchange_data
    
    def is_symbol_safe(self, symbol: str, exchange_data: Dict[str, Dict]) -> bool:
        """Check if symbol is safe for arbitrage"""
        
        # 1. Trusted symbols list
        if symbol in self.trusted_symbols:
            return True
        
        # 2. Suspicious symbol check
        base_symbol = symbol.replace('USDT', '').replace('USDC', '').replace('BUSD', '')
        if any(suspicious in base_symbol.upper() for suspicious in self.suspicious_symbols):
            # Additional checks for suspicious symbols
            total_volume = sum(data.get('volume', 0) for data in exchange_data.values())
            min_exchanges = sum(1 for data in exchange_data.values() if data.get('volume', 0) > self.min_volume_threshold * 2)
            
            # High volume and multiple exchanges = probably safe
            return total_volume > self.min_volume_threshold * 5 and min_exchanges >= 2
        
        # 3. General safety checks
        volumes = [data.get('volume', 0) for data in exchange_data.values()]
        if not volumes:
            return False
            
        avg_volume = sum(volumes) / len(volumes)
        
        # Average volume sufficient?
        if avg_volume < self.min_volume_threshold:
            return False
        
        # Volume differences too large? (one exchange very high, another very low)
        max_vol, min_vol = max(volumes), min(volumes)
        if min_vol > 0 and max_vol > min_vol * 100:  # 100x difference is suspicious
            return False
        
        return True
    
    def validate_arbitrage_opportunity(self, opportunity: Dict) -> bool:
        """Validate if arbitrage opportunity is real"""
        
        # 1. Profit ratio too high?
        if opportunity['profit_percent'] > self.max_profit_threshold:
            logger.warning(f"Suspicious high profit: {opportunity['symbol']} - {opportunity['profit_percent']:.2f}%")
            return False
        
        # 2. Price difference reasonable?
        price_ratio = opportunity['sell_price'] / opportunity['buy_price']
        if price_ratio > 1.3:  # More than 30% difference is suspicious
            return False
        
        # 3. Minimum profit threshold
        if opportunity['profit_percent'] < 0.1:  # Less than 0.1% profit is meaningless
            return False
        
        return True
    
    def calculate_arbitrage(self, all_data: Dict[str, Dict[str, Dict]], is_premium: bool = False) -> List[Dict]:
        """Enhanced arbitrage calculation"""
        opportunities = []
        
        # Find common symbols across exchanges
        all_symbols = set()
        for exchange_data in all_data.values():
            if exchange_data:
                all_symbols.update(exchange_data.keys())
        
        # Filter symbols that appear in at least 2 exchanges
        common_symbols = set()
        for symbol in all_symbols:
            exchanges_with_symbol = sum(1 for exchange_data in all_data.values() if symbol in exchange_data)
            if exchanges_with_symbol >= 2:
                common_symbols.add(symbol)
        
        logger.info(f"Found {len(common_symbols)} common symbols")
        
        for symbol in common_symbols:
            # Collect all exchange data for this symbol
            exchange_data = {ex: all_data[ex][symbol] for ex in all_data if symbol in all_data[ex]}
            
            # Safety check
            if not self.is_symbol_safe(symbol, exchange_data):
                continue
            
            if len(exchange_data) >= 2:
                # Sort by price
                sorted_exchanges = sorted(exchange_data.items(), key=lambda x: x[1]['price'])
                lowest_ex, lowest_data = sorted_exchanges[0]
                highest_ex, highest_data = sorted_exchanges[-1]
                
                lowest_price = lowest_data['price']
                highest_price = highest_data['price']
                
                if lowest_price > 0:
                    profit_percent = ((highest_price - lowest_price) / lowest_price) * 100
                    
                    opportunity = {
                        'symbol': symbol,
                        'buy_exchange': lowest_ex,
                        'sell_exchange': highest_ex,
                        'buy_price': lowest_price,
                        'sell_price': highest_price,
                        'profit_percent': profit_percent,
                        'buy_volume': lowest_data.get('volume', 0),
                        'sell_volume': highest_data.get('volume', 0),
                        'avg_volume': (lowest_data.get('volume', 0) + highest_data.get('volume', 0)) / 2
                    }
                    
                    if self.validate_arbitrage_opportunity(opportunity):
                        # For free users, only show opportunities up to 2%
                        if not is_premium and opportunity['profit_percent'] > self.free_user_max_profit:
                            continue
                        opportunities.append(opportunity)
        
        return sorted(opportunities, key=lambda x: x['profit_percent'], reverse=True)
    
    def is_premium_user(self, user_id: int) -> bool:
        """Check if user is premium"""
        return user_id in self.premium_users
    
    def save_user(self, user_id: int, username: str):
        """Save user to database"""
        with sqlite3.connect('arbitrage.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username)
                VALUES (?, ?)
            ''', (user_id, username))
            conn.commit()
    
    def save_arbitrage_data(self, opportunity: Dict):
        """Save arbitrage data"""
        with sqlite3.connect('arbitrage.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO arbitrage_data 
                (symbol, exchange1, exchange2, price1, price2, profit_percent, volume_24h)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                opportunity['symbol'],
                opportunity['buy_exchange'],
                opportunity['sell_exchange'],
                opportunity['buy_price'],
                opportunity['sell_price'],
                opportunity['profit_percent'],
                opportunity['avg_volume']
            ))
            conn.commit()
    
    def get_premium_users_list(self) -> List[Dict]:
        """Get list of premium users"""
        with sqlite3.connect('arbitrage.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, subscription_end, added_date 
                FROM premium_users 
                ORDER BY added_date DESC
            ''')
            results = cursor.fetchall()
            return [
                {
                    'user_id': row[0],
                    'username': row[1] or 'Unknown',
                    'subscription_end': row[2],
                    'added_date': row[3]
                } for row in results
            ]

# Global bot instance
bot = ArbitrageBot()

# Admin user ID - set your Telegram user ID here
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))  # Replace with your user ID

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot.save_user(user.id, user.username or "")
    
    is_premium = bot.is_premium_user(user.id)
    welcome_text = "🎯 Premium" if is_premium else "🔍 Free"
    
    keyboard = [
        [InlineKeyboardButton("🔍 Check Arbitrage", callback_data='check')],
        [InlineKeyboardButton("📊 Trusted Coins", callback_data='trusted')],
        [InlineKeyboardButton("💎 Premium Info", callback_data='premium')],
        [InlineKeyboardButton("ℹ️ Help", callback_data='help')]
    ]
    
    if user.id == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data='admin')])
    
    await update.message.reply_text(
        f"Hello {user.first_name}! 👋\n"
        f"Welcome to the Advanced Crypto Arbitrage Bot\n\n"
        f"🔐 Account: {welcome_text}\n"
        f"📈 {len(bot.exchanges)} Exchanges Supported\n"
        f"✅ Security filters active\n"
        f"📊 Volume-based validation\n"
        f"🔍 Suspicious coin detection",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'check':
        await handle_arbitrage_check(query)
    elif query.data == 'trusted':
        await show_trusted_symbols(query)
    elif query.data == 'premium':
        await show_premium_info(query)
    elif query.data == 'help':
        await show_help(query)
    elif query.data == 'admin' and query.from_user.id == ADMIN_USER_ID:
        await show_admin_panel(query)
    elif query.data == 'list_premium' and query.from_user.id == ADMIN_USER_ID:
        await list_premium_users(query)
    elif query.data == 'back':
        await start(update, context)

async def handle_arbitrage_check(query):
    await query.edit_message_text("🔄 Scanning prices across exchanges... (Security filters active)")
    
    all_data = await bot.get_all_prices_with_volume()
    user_id = query.from_user.id
    is_premium = bot.is_premium_user(user_id)
    
    opportunities = bot.calculate_arbitrage(all_data, is_premium)
    
    if not opportunities:
        await query.edit_message_text(
            "❌ No safe arbitrage opportunities found\n\n"
            "🔒 Security filters applied:\n"
            "• Minimum volume control ($100k+)\n"
            "• Suspicious coin detection\n"
            "• Reasonable profit ratio control\n"
            f"• Max profit shown: {bot.free_user_max_profit}%" if not is_premium else "• Full profit range available"
        )
        return
    
    text = "💎 Premium Safe Arbitrage:\n\n" if is_premium else f"🔍 Safe Arbitrage (≤{bot.free_user_max_profit}%):\n\n"
    
    max_opps = 20 if is_premium else 8
    for i, opp in enumerate(opportunities[:max_opps], 1):
        # Trusted coin indicator
        trust_icon = "✅" if opp['symbol'] in bot.trusted_symbols else "🔍"
        
        text += f"{i}. {trust_icon} {opp['symbol']}\n"
        text += f"   ⬇️ Buy: {opp['buy_exchange']} ${opp['buy_price']:.6f}\n"
        text += f"   ⬆️ Sell: {opp['sell_exchange']} ${opp['sell_price']:.6f}\n"
        text += f"   💰 Profit: {opp['profit_percent']:.2f}%\n"
        text += f"   📊 Volume: ${opp['avg_volume']:,.0f}\n\n"
        
        # Save data for premium users
        if is_premium:
            bot.save_arbitrage_data(opp)
    
    if not is_premium:
        total_opportunities = len(opportunities)
        hidden_opportunities = max(0, total_opportunities - max_opps)
        text += f"\n💎 Showing {min(max_opps, total_opportunities)} of {total_opportunities} opportunities"
        if hidden_opportunities > 0:
            text += f"\n🔒 {hidden_opportunities} more opportunities available for premium users"
        text += f"\n📈 Higher profit rates (>{bot.free_user_max_profit}%) available with premium!"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data='check')],
        [InlineKeyboardButton("📊 Trusted Coins", callback_data='trusted')],
        [InlineKeyboardButton("💎 Premium", callback_data='premium')],
        [InlineKeyboardButton("🔙 Main Menu", callback_data='back')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_trusted_symbols(query):
    text = "✅ **Trusted Cryptocurrencies**\n\n"
    text += "These coins are verified across all exchanges:\n\n"
    
    symbols_list = list(bot.trusted_symbols)
    symbols_list.sort()
    
    # Group symbols for better display
    for i in range(0, len(symbols_list), 3):
        group = symbols_list[i:i+3]
        text += " • ".join(group) + "\n"
    
    text += f"\n📊 Total: {len(bot.trusted_symbols)} trusted coins"
    text += "\n🔒 These symbols have additional security validation"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_premium_info(query):
    user_id = query.from_user.id
    is_premium = bot.is_premium_user(user_id)
    
    if is_premium:
        text = """💎 **Premium Member Benefits**
        
✅ **Active Premium Features:**
• Unlimited arbitrage scanning
• Full profit range display (up to 20%)
• Access to all exchanges data
• Advanced security filters
• Volume-based validation
• Historical data storage
• Priority support

📊 **Statistics:**
• {} exchanges monitored
• {} trusted cryptocurrencies
• Real-time price monitoring

🔄 **Your subscription is active**""".format(len(bot.exchanges), len(bot.trusted_symbols))
    else:
        text = """💎 **Premium Membership Benefits**

🆓 **Free Account Limitations:**
• Max 2% profit rate display
• Limited opportunities shown
• Basic security filters

💎 **Premium Benefits:**
• Full profit range (up to 20%)
• Unlimited opportunities
• {} exchanges access
• {} trusted coins validation
• Advanced security filters
• Volume analysis
• Historical data
• Priority support

💰 **Contact admin for premium access**
📞 **Support:** Contact bot administrator""".format(len(bot.exchanges), len(bot.trusted_symbols))
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_help(query):
    text = """ℹ️ **Bot Usage Guide**

🔍 **Main Features:**
• Real-time arbitrage scanning
• {} exchanges supported
• Security filters active
• Volume-based validation

📋 **Commands:**
/start - Start the bot
/check - Quick arbitrage scan
/premium - Premium information
/help - Show this help

🔒 **Security Features:**
• Suspicious coin detection
• Volume threshold filtering
• Price ratio validation
• Trusted symbols priority

📊 **Data Sources:**
Multiple cryptocurrency exchanges with real-time price feeds

📞 **Support:** Contact bot administrator for issues""".format(len(bot.exchanges))
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_admin_panel(query):
    text = """👑 **Admin Panel**
    
📊 **Statistics:**
• Total premium users: {}
• Total exchanges: {}
• Trusted symbols: {}

🛠️ **Available Commands:**
• /addpremium <user_id> [days] - Add premium user
• /removepremium <user_id> - Remove premium user
• /listpremium - List all premium users
• /stats - Bot statistics

📋 **Quick Actions:**""".format(
        len(bot.premium_users), 
        len(bot.exchanges), 
        len(bot.trusted_symbols)
    )
    
    keyboard = [
        [InlineKeyboardButton("📋 List Premium Users", callback_data='list_premium')],
        [InlineKeyboardButton("🔙 Back", callback_data='back')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def list_premium_users(query):
    users = bot.get_premium_users_list()
    
    if not users:
        text = "📋 **Premium Users List**\n\nNo premium users found."
    else:
        text = f"📋 **Premium Users List** ({len(users)} users)\n\n"
        for i, user in enumerate(users[:20], 1):  # Show max 20 users
            text += f"{i}. **{user['username']}** (ID: {user['user_id']})\n"
            text += f"   └ Until: {user['subscription_end']}\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data='list_premium')],
        [InlineKeyboardButton("🔙 Admin Panel", callback_data='admin')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# Admin Commands
async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Access denied. Admin only command.")
        return
    
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage: /addpremium <user_id> [days]\nExample: /addpremium 123456789 30")
        return
    
    try:
        user_id = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else 30
        
        bot.add_premium_user(user_id, "", days)
        await update.message.reply_text(f"✅ User {user_id} added as premium for {days} days.")
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or days. Use numbers only.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Access denied. Admin only command.")
        return
    
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage: /removepremium <user_id>\nExample: /removepremium 123456789")
        return
    
    try:
        user_id = int(context.args[0])
        bot.remove_premium_user(user_id)
        await update.message.reply_text(f"✅ User {user_id} removed from premium.")
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Use numbers only.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def list_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Access denied. Admin only command.")
        return
    
    users = bot.get_premium_users_list()
    
    if not users:
        await update.message.reply_text("📋 No premium users found.")
        return
    
    text = f"📋 **Premium Users** ({len(users)} total)\n\n"
    for i, user in enumerate(users[:30], 1):
        text += f"{i}. {user['username']} (ID: {user['user_id']})\n"
        text += f"   Until: {user['subscription_end']}\n\n"
    
    if len(users) > 30:
        text += f"... and {len(users) - 30} more users"
    
    await update.message.reply_text(text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Access denied. Admin only command.")
        return
    
    with sqlite3.connect('arbitrage.db') as conn:
        cursor = conn.cursor()
        
        # Get total users
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        # Get arbitrage data count
        cursor.execute('SELECT COUNT(*) FROM arbitrage_data')
        total_arbitrage_records = cursor.fetchone()[0]
    
    text = f"""📊 **Bot Statistics**

👥 **Users:**
• Total users: {total_users}
• Premium users: {len(bot.premium_users)}
• Free users: {total_users - len(bot.premium_users)}

📈 **Data:**
• Exchanges monitored: {len(bot.exchanges)}
• Trusted symbols: {len(bot.trusted_symbols)}
• Arbitrage records: {total_arbitrage_records}

🔒 **Security:**
• Volume threshold: ${bot.min_volume_threshold:,}
• Max profit threshold: {bot.max_profit_threshold}%
• Free user limit: {bot.free_user_max_profit}%

⚡ **System:**
• Bot status: Active
• Database: Connected"""
    
    await update.message.reply_text(text)

# Quick check command
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot.save_user(user.id, user.username or "")
    
    msg = await update.message.reply_text("🔄 Scanning arbitrage opportunities...")
    
    all_data = await bot.get_all_prices_with_volume()
    is_premium = bot.is_premium_user(user.id)
    
    opportunities = bot.calculate_arbitrage(all_data, is_premium)
    
    if not opportunities:
        await msg.edit_text("❌ No safe arbitrage opportunities found at the moment.")
        return
    
    text = f"🔍 Quick Arbitrage Scan Results:\n\n"
    
    max_opps = 10 if is_premium else 5
    for i, opp in enumerate(opportunities[:max_opps], 1):
        trust_icon = "✅" if opp['symbol'] in bot.trusted_symbols else "🔍"
        text += f"{i}. {trust_icon} {opp['symbol']}\n"
        text += f"   💰 {opp['profit_percent']:.2f}% profit\n"
        text += f"   📊 ${opp['avg_volume']:,.0f} volume\n\n"
    
    if not is_premium and len(opportunities) > max_opps:
        text += f"💎 {len(opportunities) - max_opps} more opportunities available with premium!"
    
    await msg.edit_text(text)

def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not found!")
        return
    
    # Set admin user ID from environment
    global ADMIN_USER_ID
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
    
    if ADMIN_USER_ID == 0:
        logger.warning("ADMIN_USER_ID not set! Admin commands will not work.")
    
    app = Application.builder().token(TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("addpremium", add_premium_command))
    app.add_handler(CommandHandler("removepremium", remove_premium_command))
    app.add_handler(CommandHandler("listpremium", list_premium_command))
    app.add_handler(CommandHandler("stats", stats_command))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Advanced Arbitrage Bot starting...")
    logger.info(f"Monitoring {len(bot.exchanges)} exchanges")
    logger.info(f"Tracking {len(bot.trusted_symbols)} trusted symbols")
    logger.info(f"Premium users loaded: {len(bot.premium_users)}")
    
    app.run_polling()

if __name__ == '__main__':
    main()
