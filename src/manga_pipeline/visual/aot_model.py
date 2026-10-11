"""AIP419's AOT checkpoint architecture, separated from stage orchestration.

Source: Ximm0502/AIP419/core/aot_model.py and mayocream/aot-inpainting.
Layer names are preserved for strict safetensors checkpoint compatibility.
"""
import math
import torch
from torch import nn
from torch.nn import functional as F


class _ScaledWSConv2d(nn.Conv2d):
    def __init__(self, *args, gain=True, eps=1e-4, **kwargs):
        super().__init__(*args, **kwargs)
        self.gain = nn.Parameter(torch.ones(self.out_channels,1,1,1)) if gain else None
        self.eps = eps

    def forward(self, value):
        variance,mean = torch.var_mean(self.weight,dim=(1,2,3),keepdim=True)
        scale = torch.rsqrt(torch.clamp(variance*math.prod(self.weight.shape[1:]),min=self.eps))
        if self.gain is not None:
            scale = scale*self.gain
        return F.conv2d(value,self.weight*scale-mean*scale,self.bias,self.stride,self.padding,self.dilation,self.groups)


class _ScaledWSTransposeConv2d(nn.ConvTranspose2d):
    def __init__(self,*args,gain=True,eps=1e-4,**kwargs):
        super().__init__(*args,**kwargs)
        self.gain = nn.Parameter(torch.ones(self.in_channels,1,1,1)) if gain else None
        self.eps = eps

    def forward(self,value):
        variance,mean = torch.var_mean(self.weight,dim=(1,2,3),keepdim=True)
        scale = torch.rsqrt(torch.clamp(variance*math.prod(self.weight.shape[1:]),min=self.eps))
        if self.gain is not None:
            scale = scale*self.gain
        return F.conv_transpose2d(value,self.weight*scale-mean*scale,self.bias,self.stride,
                                 self.padding,self.output_padding,self.groups,self.dilation)


class _GatedWSConvPadded(nn.Module):
    def __init__(self,in_channels,out_channels,kernel_size,stride=1):
        super().__init__()
        self.padding = nn.ReflectionPad2d((kernel_size-1)//2)
        self.conv = _ScaledWSConv2d(in_channels,out_channels,kernel_size,stride=stride)
        self.conv_gate = _ScaledWSConv2d(in_channels,out_channels,kernel_size,stride=stride)

    def forward(self,value):
        value = self.padding(value)
        return self.conv(value)*torch.sigmoid(self.conv_gate(value))*1.8


class _GatedWSTransposeConvPadded(nn.Module):
    def __init__(self,in_channels,out_channels,kernel_size,stride):
        super().__init__()
        padding = (kernel_size-1)//2
        self.conv = _ScaledWSTransposeConv2d(in_channels,out_channels,kernel_size,stride=stride,padding=padding)
        self.conv_gate = _ScaledWSTransposeConv2d(in_channels,out_channels,kernel_size,stride=stride,padding=padding)

    def forward(self,value):
        return self.conv(value)*torch.sigmoid(self.conv_gate(value))*1.8


class _AOTBlock(nn.Module):
    def __init__(self,channels,dilation_rates=(2,4,8,16)):
        super().__init__()
        for index,rate in enumerate(dilation_rates):
            setattr(self,f"block{index:02d}",nn.Sequential(nn.ReflectionPad2d(rate),
                    nn.Conv2d(channels,channels//4,3,dilation=rate),nn.ReLU(inplace=True)))
        self.fuse = nn.Sequential(nn.ReflectionPad2d(1),nn.Conv2d(channels,channels,3))
        self.gate = nn.Sequential(nn.ReflectionPad2d(1),nn.Conv2d(channels,channels,3))

    def forward(self,value):
        output = self.fuse(torch.cat([getattr(self,f"block{i:02d}")(value) for i in range(4)],dim=1))
        gate = self.gate(value)
        mean,std = gate.mean((2,3),keepdim=True),gate.std((2,3),keepdim=True)+1e-9
        gate = torch.sigmoid(5*(2*(gate-mean)/std-1))
        return value*(1-gate)+output*gate


class _ScaledReLU(nn.Module):
    def forward(self,value):
        return F.relu(value)*1.7139588594436646


class AOTGenerator(nn.Module):
    def __init__(self):
        super().__init__()
        self.head = nn.Sequential(_GatedWSConvPadded(4,32,3),_ScaledReLU(),
                                 _GatedWSConvPadded(32,64,4,stride=2),_ScaledReLU(),
                                 _GatedWSConvPadded(64,128,4,stride=2))
        self.body_conv = nn.Sequential(*[_AOTBlock(128) for _ in range(10)])
        self.tail = nn.Sequential(_GatedWSConvPadded(128,128,3),_ScaledReLU(),
                                 _GatedWSConvPadded(128,128,3),_ScaledReLU(),
                                 _GatedWSTransposeConvPadded(128,64,4,stride=2),_ScaledReLU(),
                                 _GatedWSTransposeConvPadded(64,32,4,stride=2),_ScaledReLU(),
                                 _GatedWSConvPadded(32,3,3))

    def forward(self,image,mask):
        return self.tail(self.body_conv(self.head(torch.cat((mask,image),dim=1)))).clamp(-1,1)
