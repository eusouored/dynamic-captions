"""Exceção única de cancelamento, usada pela transcrição, pela renderização e pela fila."""


class Cancelled(Exception):
    """O trabalho foi cancelado a pedido do usuário (não é falha)."""
