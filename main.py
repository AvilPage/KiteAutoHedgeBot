"""
Build a python simple gui to log into Zerodha(using jugaad-trader) using username, password & totp.
Look at open positions and then place hedge orders for open positions.
"""

import json
import os
import string
import time
import traceback
from pprint import pprint
from typing import Iterable

import PySimpleGUI as sg
import pandas as pd
from jugaad_trader import Zerodha
from platformdirs import user_data_dir

debug = False


window = None
font = ('SF Pro', 15)
sg.theme('SystemDefault')

kite = Zerodha()

BLANK_BOX = '☐'
CHECKED_BOX = '☑'

app_name = 'KiteAutoHedgeBot'
app_author = 'AvilPage'
user_data_dir = user_data_dir(app_name, app_author)
settings_file = os.path.join(user_data_dir, 'AutoHedger.json')
try:
    settings = json.load(open(settings_file, 'r'))
except Exception:
    settings = {}

if not os.path.exists(user_data_dir):
    os.makedirs(user_data_dir)

if not os.path.exists(settings_file):
    with open(settings_file, 'w') as f:
        json.dump({}, f, indent=4)



def get_layout():
    input_text_size = (10, 1)
    layout = [
        [
            sg.Text('username', size=(9, 1)), sg.InputText(size=input_text_size, key='username', default_text=''),
            sg.Text('password', size=(9, 1)), sg.InputText(size=input_text_size, key='password', password_char='*', default_text=''),
            sg.Text('totp', size=(5, 1)), sg.InputText(size=input_text_size, key='totp'),
            sg.Submit(button_text='Login', key='login'),
        ],
        [
            sg.Text('Hedge %', size=(8, 1)), sg.InputText(size=(4, 1), key='hedge_percentage', default_text='10'),
            sg.Submit(button_text='Calculate Hedges', key='calculate_hedges'),
            sg.Submit(button_text='Place Hedge Orders', key='place_hedge_orders')
        ],
        [
            sg.Table(
                key='table_ih',
                size=(200, 20),
                headings=['Stock Symbol', 'Long', 'Short', 'Hedge', 'LT_Price', 'Hedge_Price', 'Hedge_Symbol_Option_To_Buy', 'Check'],
                values=[],
                max_col_width=25,
                enable_events=True,
                # auto_size_columns=True,
            )
        ],
        [
            sg.Multiline("Output", key="output", size=(200, 10)),
        ],
    ]
    return layout


def get_instruments_df():
    instruments_filename = os.path.join(user_data_dir, 'instruments.csv')
    # get modified time of instruments.csv
    # if modified time is more than 1 day, download instruments.csv
    if os.path.exists(instruments_filename):
        modified_time = os.path.getmtime(instruments_filename)
        if modified_time + 86400 < time.time():
            print('removing instruments.csv')
            os.remove(instruments_filename)

    if not os.path.exists(instruments_filename):
        url = 'https://api.kite.trade/instruments'
        df = pd.read_csv(url)
        df.to_csv(instruments_filename)
        print('downloaded instruments.csv')

    df = pd.read_csv(instruments_filename, index_col=0)
    return df


def save_settings(user_settings: dict):
    """
    Save user settings to json file. Exclude output & tab values.
    """
    with open(settings_file, 'w') as f:
        user_settings.pop(0, None)
        user_settings.pop("totp", None)
        json.dump(user_settings, f, indent=4)


def apply_settings(window: sg.Window):
    """
    Read user settings from json file and apply to window.
    """
    if not os.path.exists(settings_file):
        return

    with open(settings_file, 'r') as f:
        user_settings = json.load(f)
    for key, value in user_settings.items():
        try:
            window[key].update(value)
        except Exception:
            continue


def window_print(*args: Iterable[str]):
    global window
    output = window["output"]
    output.update(output.get() + f"\n{' '.join(str(i) for i in args)}")
    window.refresh()


def try_auto_login():
    settings = json.load(open(settings_file, 'r'))
    enc_token = settings.get('enc_token')
    if not enc_token:
        return

    try:
        kite = Zerodha()
        kite.enc_token = enc_token
        profile = kite.profile()
        window_print(f'\nWelcome, {profile["user_name"]}!')
        return kite
    except Exception as e:
        window_print(f'Error: {e}')


def login(values):
    try:
        kite = Zerodha(user_id=values['username'].upper(), password=values['password'], twofa=values['totp'])
        kite.login()
        profile = kite.profile()
        pprint(profile)
        # after login, add text to show that user is looged in
        window_print(f'\nWelcome, {profile["user_name"]}!')
        settings['enc_token'] = kite.enc_token
        save_settings(settings)
        return kite
    except Exception as e:
        window_print(f'Error: {e}')


def place_order(data):
    try:
        quantity = 1

        order_id = kite.place_order(
            exchange=kite.EXCHANGE_NSE,
            variety=kite.VARIETY_REGULAR,
            order_type=kite.ORDER_TYPE_MARKET,
            product=kite.PRODUCT_CNC,
            validity=kite.VALIDITY_DAY,
            tradingsymbol=data['tradingsymbol'],
            transaction_type=data['transaction_type'],
            quantity=quantity,
            tag=app_name,
        )
        message = "Order placed. ID is: {}".format(order_id)
        window_print(message)
    except Exception as e:
        traceback.print_exc()
        message = "Order placement failed: {} {}".format(data['tradingsymbol'], str(e))
        window_print(message)


def get_hedge(future, calls, puts):
    """
    hedge - protected option for given future at a distance ot 10% from current price
    future format - PIDILITIND23DECFUT
    call format - PIDILITIND23DEC1400CE
    put format - PIDILITIND23DEC1400PE

    # match based on the name excluding strike & last CE, PE, FUT
    # split tradingsymbol by the digits at the end
    # check if the first part of the split matches
    """
    future_long = future['quantity'] > 0
    future_sub = future['tradingsymbol'].strip('FUT')

    if future_long:
        for put in puts:
            put_sub = put['tradingsymbol'].strip('PE').strip(string.digits)
            if future_sub == put_sub:
                return put

    if not future_long:
        for call in calls:
            call_sub = call['tradingsymbol'].strip('CE').strip(string.digits)
            if future_sub == call_sub:
                return call


def get_open_positions():
    if debug:
        open_positions = json.load(open('positions.json', 'r'))
    else:
        open_positions = kite.positions()

    open_positions = open_positions['net']
    open_positions = sorted(open_positions, key=lambda x: x['tradingsymbol'])
    return open_positions


def position_type(position):
    """
    position - dict
    """
    is_future = position['tradingsymbol'].endswith('FUT')
    if is_future:
        return 'LONG' if position['quantity'] > 0 else 'SHORT'
    else:
        is_call = position['tradingsymbol'].endswith('CE')
        if is_call:
            return 'LONG' if position['quantity'] > 0 else 'SHORT'
        else:
            return 'LONG' if position['quantity'] < 0 else 'SHORT'


def is_hedge(position):
    if position['is_future']:
        return False

    if position['quantity'] < 0:
        return False

    return True


def lot_size(position):
    instruments_df = get_instruments_df()
    df = instruments_df[instruments_df['tradingsymbol'] == position['tradingsymbol']]
    return df['lot_size'].values[0]


def get_grouped_fno_df(group_by='tradingsymbol'):
    open_positions = get_open_positions()
    df = pd.DataFrame(open_positions)
    if df.empty:
        return df
    df = df[df['exchange'] == 'NFO']
    df['symbol'] = df['tradingsymbol'].str.extract(r'([A-Z]+)')
    df['is_future'] = df['tradingsymbol'].str.endswith('FUT')
    df['is_call'] = df['tradingsymbol'].str.endswith('CE')
    df['is_put'] = df['tradingsymbol'].str.endswith('PE')

    df['position_type'] = df.apply(lambda x: position_type(x), axis=1)
    df['is_hedge'] = df.apply(lambda x: is_hedge(x), axis=1)
    df['lot_size'] = df.apply(lambda x: lot_size(x), axis=1)

    df['lots'] = abs(df['quantity'] // df['lot_size'])
    fdf = df[df['lots'] == 0]
    print(fdf)
    df = df[df['lots'] != 0]

    return df


def get_fno():
    open_positions = get_open_positions()

    futures = [i for i in open_positions if i['tradingsymbol'].endswith('FUT')]
    calls = [i for i in open_positions if i['tradingsymbol'].endswith('CE')]
    puts = [i for i in open_positions if i['tradingsymbol'].endswith('PE')]

    return futures, calls, puts


def get_nearest_option_sumbol(symbol, option_price, option_type):
    """
    option_price - 10% of future price
    """
    df = get_instruments_df()
    options = df[df['tradingsymbol'].str.contains(symbol)]
    options = options[options['instrument_type'] == option_type]
    options['strike'] = options['tradingsymbol'].str.rstrip(option_type)
    options['strike'] = options['strike'].str.split(r'(\d+)$').str[1].astype(float)
    # find the nearest strike price
    options['diff'] = options['strike'].apply(lambda x: abs(x - option_price))
    options = options.sort_values(by=['diff'])

    return options['tradingsymbol'].values[0], round(options['strike'].values[0])


def calculate_hedge(future):
    tradesymbol = future['tradingsymbol']
    symbol = tradesymbol.strip('FUT')
    ltp = future['last_price']
    if future['type'] == 'LONG':
        option_price = ltp * 0.8
        option_type = 'PE'
    else:
        option_price = ltp * 1.2
        option_type = 'CE'
    option_price = round(option_price, 2)
    option_symbol, option_strike = get_nearest_option_sumbol(symbol, option_price, option_type)
    return option_symbol, option_strike


def get_ltp(symbol):
    new_symbol = f'NSE:{symbol}'
    if not kite:
        window_print('Login first.')
    ltp_dict = kite.ltp(new_symbol)[new_symbol]
    return ltp_dict['last_price']


def get_hedge_option(symbol, option_type, hedge_percentage):
    ltp = round(get_ltp(symbol))
    if option_type == 'PE':
        option_price = ltp * (100 - hedge_percentage) / 100
    else:
        option_price = ltp * (100 + hedge_percentage) / 100

    option_symbol, option_strike = get_nearest_option_sumbol(symbol, option_price, option_type)
    return ltp, option_strike, option_symbol


def calculate_hedges(values=None):
    if not kite:
        window_print('Login first.')
        return

    df = get_grouped_fno_df(group_by='tradingsymbol')
    if df.empty:
        window_print('No open positions found.')
        return

    window_print('Checking hedges...')
    try:
        hedge_percentage = int(values['hedge_percentage'])
    except Exception:
        hedge_percentage = 10

    data = []
    long_short_count = {}
    for key, group in df.groupby(['symbol', 'position_type']):
        if key[0] not in long_short_count:
            long_short_count[key[0]] = {}
        long_short_count[key[0]][key[1]] = group['lots'].sum()

        for item in group.to_dict(orient='records'):
            row = [
                item['tradingsymbol'], item['quantity'], item['position_type'], item['lots'], item['is_hedge'],
            ]
            window_print(row)
            data.append(row)

    window_print(long_short_count)

    global table_ih
    for key, value in long_short_count.items():
        long = value.get('LONG', 0)
        short = value.get('SHORT', 0)
        diff = long - short

        if diff == 0:
            continue

        option_type = 'PE' if diff > 0 else 'CE'
        ltp, hedge_strike, hedge_option = get_hedge_option(key, option_type, hedge_percentage)
        row = [key, long, short, diff, ltp, hedge_strike, hedge_option, CHECKED_BOX]

        # window_print(row)
        table_ih.append(row)

    # window_print(table_ih)
    window['table_ih'].update(values=table_ih)


def place_hedge_orders(values):
    window_print('Placing hedge orders...')
    debug = values.get('debug')
    selected_hedges = [i for i in table_ih[1:][:] if i[-1] == CHECKED_BOX]

    if debug:
        window_print(selected_hedges)
        return

    for hedge in selected_hedges:
        symbol = hedge[0]
        count = hedge[-2]
        option_symbol = hedge[-3]
        option_strike = hedge[-4]
        window_print(f'{symbol} {count} {option_symbol} {option_strike}')

        data = {'tradingsymbol': option_symbol, 'transaction_type': 'BUY', 'quantity': count}
        place_order(data)


table_ih = []

layout = get_layout()
window = sg.Window('Auto Hedger', layout, resizable=True, font=font, finalize=True)


window_print(f'settings_file: {settings_file}')
# window_print(f'settings: {settings}')
# apply_settings(window)
# window['output'].update(f'user_data_dir: {user_data_dir}')
kite = try_auto_login()
calculate_hedges()


event_mapper = {
    'login': login,
    'calculate_hedges': calculate_hedges,
    'place_hedge_orders': place_hedge_orders,
}

while True:
    event, values = window.read(timeout=100)

    if event == sg.WIN_CLOSED or event == "Exit":
        break

    if not values or event == '__TIMEOUT__':
        continue

    event, values = window.read()

    if event == 'table_ih' and values[event]:
        row = values[event][0] + 1
        table_ih[row][-1] = CHECKED_BOX if table_ih[row][-1] == BLANK_BOX else BLANK_BOX
        window['table_ih'].update(values=table_ih[1:][:])
        selected_hedges = [i for i in table_ih[1:][:] if i[-1] == CHECKED_BOX]
        window_print(selected_hedges)

    window_print(event)

    action = event_mapper.get(event)
    if not action:
        continue

    # result = action()
    result = action(values)

    if action == login:
        kite = result
