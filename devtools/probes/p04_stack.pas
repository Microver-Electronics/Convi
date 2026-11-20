procedure DumpStack(Board: Variant);
var LS, LO: Variant;
begin
  LS := Board.LayerStack_V7;
  LO := LS.FirstLayer;
  while LO <> Nil do
  begin
    L('  layer ' + LO.Name + ' id=' + IntToStr(LO.V7_LayerID.ID) + ' cu=' + FloatToStr(CoordToMils(LO.CopperThickness)));
    try
      L('     diel h=' + FloatToStr(CoordToMils(LO.Dielectric.DielectricHeight)) + ' type=' + IntToStr(LO.Dielectric.DielectricType) + ' mat=' + LO.Dielectric.DielectricMaterial);
    except
      L('     (no dielectric)');
    end;
    LO := LS.NextLayer(LO);
  end;
end;

procedure Body;
var
  Doc, Board, LS, LO, M: Variant;
  i: Integer;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  L('default stack:');
  DumpStack(Board);
  LS := Board.LayerStack_V7;
  try
    LS.InsertLayer(eMidLayer1);
    LS.InsertLayer(eMidLayer2);
    L('inserted 2 mid layers');
  except
    L('InsertLayer failed: ' + ExceptionMessage);
  end;
  DumpStack(Board);
  try
    M := LS.LayerObject_V7[ILayer.MechanicalLayer(13)];
    L('mech13 name=' + M.Name + ' enabled=' + BoolToStr(M.MechanicalLayerEnabled, True));
    M.MechanicalLayerEnabled := True;
    M.Name := 'Assembly Top';
    M.Kind := mlAssemblyTop;
    L('mech13 set kind ok');
  except
    L('mech failed: ' + ExceptionMessage);
  end;
  try
    M := LS.LayerObject_V7[ILayer.MechanicalLayer(40)];
    M.MechanicalLayerEnabled := True;
    M.Name := 'Mech40Test';
    L('mech40 ok');
  except
    L('mech40 failed: ' + ExceptionMessage);
  end;
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p04.PcbDoc');
  Doc.DoFileSave('');
  Client.CloseDocument(Doc);
end;
