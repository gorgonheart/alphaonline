"""手动加密API配置。导入不读取凭据；沿用框架AESEncryptor格式。"""
from getpass import getpass

from core.utils.encryptor import AESEncryptor


def encrypt_credentials(key: str, apikey: str, secret: str, password: str = '') -> dict[str, str]:
    encryptor = AESEncryptor(key)
    values = {'apiKey': apikey, 'secret': secret}
    if password:
        values['password'] = password
    return {name: encryptor.encrypt(value) for name, value in values.items()}


def main():
    key = getpass('加密口令: ')
    if not key:
        raise ValueError('加密口令不能为空')
    result = encrypt_credentials(key, getpass('API key: '), getpass('API secret: '),
                                 getpass('OKX password/passphrase（其他交易所留空）: '))
    for name, value in result.items():
        print(f'加密后 {name}: {value}')


if __name__ == '__main__':
    main()
