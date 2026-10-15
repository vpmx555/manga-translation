"""PyTorch AOT generator used by the manga inpainting checkpoint."""


import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class _ScaledWSConv2d(nn.Conv2d):
    def __init__(self, *args, gain: bool = True, eps: float = 1e-4, **kwargs):
        super().__init__(*args, **kwargs)
        self.gain = nn.Parameter(torch.ones(self.out_channels, 1, 1, 1)) if gain else None
        self.eps = eps

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        fan_in = np.prod(self.weight.shape[1:])
        variance, mean = torch.var_mean(self.weight, dim=(1, 2, 3), keepdim=True)
        scale = torch.rsqrt(torch.clamp(variance * fan_in, min=self.eps))
        if self.gain is not None:
            scale = scale * self.gain
        weight = self.weight * scale - mean * scale
        return F.conv2d(
            value, weight, self.bias, self.stride, self.padding, self.dilation, self.groups
        )


class _ScaledWSTransposeConv2d(nn.ConvTranspose2d):
    def __init__(self, *args, gain: bool = True, eps: float = 1e-4, **kwargs):
        super().__init__(*args, **kwargs)
        self.gain = nn.Parameter(torch.ones(self.in_channels, 1, 1, 1)) if gain else None
        self.eps = eps

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        fan_in = np.prod(self.weight.shape[1:])
        variance, mean = torch.var_mean(self.weight, dim=(1, 2, 3), keepdim=True)
        scale = torch.rsqrt(torch.clamp(variance * fan_in, min=self.eps))
        if self.gain is not None:
            scale = scale * self.gain
        weight = self.weight * scale - mean * scale
        return F.conv_transpose2d(
            value,
            weight,
            self.bias,
            self.stride,
            self.padding,
            self.output_padding,
            self.groups,
            self.dilation,
        )


class _GatedWSConvPadded(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int, stride: int = 1):
        super().__init__()
        self.padding = nn.ReflectionPad2d((kernel_size - 1) // 2)
        self.conv = _ScaledWSConv2d(in_channels, out_channels, kernel_size, stride=stride)
        self.conv_gate = _ScaledWSConv2d(in_channels, out_channels, kernel_size, stride=stride)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        value = self.padding(value)
        return self.conv(value) * torch.sigmoid(self.conv_gate(value)) * 1.8


class _GatedWSTransposeConvPadded(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int, stride: int):
        super().__init__()
        padding = (kernel_size - 1) // 2
        self.conv = _ScaledWSTransposeConv2d(
            in_channels, out_channels, kernel_size, stride=stride, padding=padding
        )
        self.conv_gate = _ScaledWSTransposeConv2d(
            in_channels, out_channels, kernel_size, stride=stride, padding=padding
        )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return self.conv(value) * torch.sigmoid(self.conv_gate(value)) * 1.8


class _AOTBlock(nn.Module):
    def __init__(self, channels: int, dilation_rates: tuple[int, ...] = (2, 4, 8, 16)):
        super().__init__()
        for index, rate in enumerate(dilation_rates):
            setattr(
                self,
                f"block{index:02d}",
                nn.Sequential(
                    nn.ReflectionPad2d(rate),
                    nn.Conv2d(channels, channels // 4, 3, dilation=rate),
                    nn.ReLU(inplace=True),
                ),
            )
        self.fuse = nn.Sequential(nn.ReflectionPad2d(1), nn.Conv2d(channels, channels, 3))
        self.gate = nn.Sequential(nn.ReflectionPad2d(1), nn.Conv2d(channels, channels, 3))

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        branches = [getattr(self, f"block{index:02d}")(value) for index in range(4)]
        output = self.fuse(torch.cat(branches, dim=1))
        gate = self.gate(value)
        mean = gate.mean((2, 3), keepdim=True)
        std = gate.std((2, 3), keepdim=True) + 1e-9
        gate = torch.sigmoid(5 * (2 * (gate - mean) / std - 1))
        return value * (1 - gate) + output * gate


class _ScaledReLU(nn.Module):
    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return F.relu(value) * 1.7139588594436646


class AOTGenerator(nn.Module):
    def __init__(self):
        super().__init__()
        self.head = nn.Sequential(
            _GatedWSConvPadded(4, 32, 3),
            _ScaledReLU(),
            _GatedWSConvPadded(32, 64, 4, stride=2),
            _ScaledReLU(),
            _GatedWSConvPadded(64, 128, 4, stride=2),
        )
        self.body_conv = nn.Sequential(*[_AOTBlock(128) for _ in range(10)])
        self.tail = nn.Sequential(
            _GatedWSConvPadded(128, 128, 3),
            _ScaledReLU(),
            _GatedWSConvPadded(128, 128, 3),
            _ScaledReLU(),
            _GatedWSTransposeConvPadded(128, 64, 4, stride=2),
            _ScaledReLU(),
            _GatedWSTransposeConvPadded(64, 32, 4, stride=2),
            _ScaledReLU(),
            _GatedWSConvPadded(32, 3, 3),
        )

    def forward(self, image: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        value = self.head(torch.cat((mask, image), dim=1))
        return self.tail(self.body_conv(value)).clamp(-1, 1)


