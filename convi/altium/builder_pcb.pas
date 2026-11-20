procedure InitSpecific;
begin
  { A test board built with online DRC off: 147 s against 138 s with it on - no gain, so the user's
    setting is left alone.  The build rate (about 1500 IR lines/s) is the script's own cost: fewer lines
    is what helps (pcb_convert.merge_fp_lines). }
  OnlineDrcWas := -1;
end;

procedure FinishSpecific;
begin
end;

{ ------------------------------------------------------------------ layers }
function LayerOf(Code: String): Integer;
var n: Integer;
begin
  if Code = 'T' then Result := eTopLayer
  else if Code = 'B' then Result := eBottomLayer
  else if Code = 'TO' then Result := eTopOverlay
  else if Code = 'BO' then Result := eBottomOverlay
  else if Code = 'TP' then Result := eTopPaste
  else if Code = 'BP' then Result := eBottomPaste
  else if Code = 'TS' then Result := eTopSolder
  else if Code = 'BS' then Result := eBottomSolder
  else if Code = 'KO' then Result := eKeepOutLayer
  else if Code = 'MULTI' then Result := eMultiLayer
  else if Copy(Code, 1, 4) = 'MECH' then
  begin
    n := StrToInt(Copy(Code, 5, 10));
    if n > 16 then n := 16;
    Result := eMechanical1 + n - 1;
  end
  else if Copy(Code, 1, 1) = 'M' then
  begin
    n := StrToInt(Copy(Code, 2, 10));
    Result := eMidLayer1 + n - 1;
  end
  else Result := eTopLayer;
end;

function NetOf(K: String): Variant;
var i: Integer;
begin
  Result := Nil;
  i := VI(K, 0);
  if (i > 0) and (i <= Nets.Count) then Result := Nets.Items[i - 1];
end;

{ ------------------------------------------------------------------ adding }
procedure AddPrim(Obj: Variant);
begin
  if Mode = 'LIB' then
  begin
    CurLibComp.AddPCBObject(Obj);
    PCBServer.SendMessageToRobots(CurLibComp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Obj.I_ObjectAddress);
  end
  else
  begin
    Board.AddPCBObject(Obj);
    if InComp then CurComp.AddPCBObject(Obj);
  end;
end;

{ ------------------------------------------------------------------ text }
procedure Justify(T: Variant; AX, AY: Integer; Rot: Double; HJ, VJ: String);
var R: TCoordRect; HX, HY: Integer;
begin
  T.Rotation := 0;
  T.XLocation := AX;
  T.YLocation := AY;
  try
    T.UpdateTextPosition;
  except
  end;
  R := T.BoundingRectangle;
  if (R.Right = R.Left) and (R.Top = R.Bottom) then
  begin
    { no bounding box available (library footprint): stroke-font estimate measured in Altium 26 }
    R.Left := AX - T.Width div 2;
    R.Right := AX + Round(Length(T.Text) * T.Size * 0.99);
    R.Bottom := AY - T.Width div 2;
    R.Top := AY + T.Size + T.Width div 2;
  end;
  if HJ = 'l' then HX := R.Left
  else if HJ = 'r' then HX := R.Right
  else HX := (R.Left + R.Right) div 2;
  if VJ = 't' then HY := R.Top
  else if VJ = 'b' then HY := R.Bottom
  else HY := (R.Bottom + R.Top) div 2;
  if (Abs(AX - HX) > 2000000) or (Abs(AY - HY) > 2000000) then
    Log('JUSTIFY-OUTLIER ' + T.Text + ' anchor=' + IntToStr(AX) + ',' + IntToStr(AY) + ' bbox=' + IntToStr(R.Left) + ',' + IntToStr(R.Bottom) + ',' + IntToStr(R.Right) + ',' + IntToStr(R.Top));
  T.MoveByXY(AX - HX, AY - HY);
  if Rot <> 0 then T.RotateAroundXY(AX, AY, Rot);
  try
    T.UpdateTextPosition;
  except
  end;
end;

procedure PlaceAt(T: Variant; AX, AY: Integer; Rot: Double);
{ TrueType text placed by its own origin: baseline through YLocation, ink from XLocation
  (to XLocation when mirrored); Convi computed that point from KiCad's rendering }
begin
  T.Rotation := 0;
  T.XLocation := AX;
  T.YLocation := AY;
  try
    T.UpdateTextPosition;
  except
  end;
  if Rot <> 0 then T.RotateAroundXY(AX, AY, Rot);
  try
    T.UpdateTextPosition;
  except
  end;
end;

procedure SetupText(T: Variant);
var HJ: String;
begin
  T.Text := V('text');
  T.Layer := LayerOf(V('l'));
  if V('font') <> '' then
  begin
    T.UseTTFonts := True;
    T.FontName := V('font');
    T.Bold := VB('bold');
    T.Italic := VB('italic');
    T.Size := VI('h', 10000);
  end
  else
  begin
    T.UseTTFonts := False;
    T.Size := VI('h', 10000);
    T.Width := VI('w', 1500);
  end;
  T.MirrorFlag := VB('mirror');
  if VB('ko') then
  begin
    try
      T.Inverted := True;
    except
    end;
  end;
end;

procedure DoText;
var T: Variant; HJ: String;
begin
  T := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
  SetupText(T);
  AddPrim(T);
  if VB('tt') then
    PlaceAt(T, CX('x'), CY('y'), VD('rot', 0))
  else
  begin
    HJ := V('hj');
    if VB('mirror') then
    begin
      if HJ = 'l' then HJ := 'r' else if HJ = 'r' then HJ := 'l';
    end;
    Justify(T, CX('x'), CY('y'), VD('rot', 0), HJ, V('vj'));
  end;
  CountStat('text');
end;

procedure DoCompText;
var T: Variant; HJ: String; Show: Boolean;
begin
  if InComp then Exit;
  if V('which') = 'name' then
  begin
    CurComp.NameAutoPosition := eAutoPos_Manual;
    T := CurComp.Name;
  end
  else
  begin
    CurComp.CommentAutoPosition := eAutoPos_Manual;
    T := CurComp.Comment;
  end;
  Show := VB('show');
  if V('which') = 'name' then CurComp.NameOn := Show else CurComp.CommentOn := Show;
  if not Has('text') then Exit;
  T.BeginModify;
  SetupText(T);
  if VB('tt') then
    PlaceAt(T, CX('x'), CY('y'), VD('rot', 0))
  else
  begin
    HJ := V('hj');
    if VB('mirror') then
    begin
      if HJ = 'l' then HJ := 'r' else if HJ = 'r' then HJ := 'l';
    end;
    Justify(T, CX('x'), CY('y'), VD('rot', 0), HJ, V('vj'));
  end;
  T.EndModify;
end;

{ ------------------------------------------------------------------ simple primitives }
procedure DoTrack;
var T: Variant;
begin
  T := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
  T.X1 := CX('x1'); T.Y1 := CY('y1');
  T.X2 := CX('x2'); T.Y2 := CY('y2');
  T.Width := VI('w', 1);
  T.Layer := LayerOf(V('l'));
  if VI('net', 0) > 0 then T.Net := NetOf('net');
  AddPrim(T);
end;

procedure DoArc;
var A: Variant;
begin
  A := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
  A.XCenter := CX('cx'); A.YCenter := CY('cy');
  A.Radius := VI('r', 1);
  A.LineWidth := VI('w', 1);
  A.StartAngle := VD('a1', 0);
  A.EndAngle := VD('a2', 360);
  A.Layer := LayerOf(V('l'));
  if VI('net', 0) > 0 then A.Net := NetOf('net');
  AddPrim(A);
end;

procedure DoVia;
var ViaObj, C: Variant; S, S2: String;
begin
  ViaObj := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
  ViaObj.X := CX('x'); ViaObj.Y := CY('y');
  ViaObj.Size := VI('d', 6000);
  ViaObj.HoleSize := VI('hole', 3000);
  ViaObj.LowLayer := LayerOf(V('lo'));
  ViaObj.HighLayer := LayerOf(V('hi'));
  if VI('net', 0) > 0 then ViaObj.Net := NetOf('net');
  S := F.Values['tentT'];
  S2 := F.Values['tentB'];
  if (S <> '') or (S2 <> '') then
  begin
    { tenting only holds on a manual solder mask expansion (a rule-driven via stays open): set it
      through the primitive cache like pads (SolderMaskExpansionMode is not a script property) }
    C := ViaObj.GetState_Cache;
    C.SolderMaskExpansion := VI('mask', 0);
    C.SolderMaskExpansionValid := eCacheManual;
    ViaObj.SetState_Cache := C;
    if S <> '' then ViaObj.IsTenting_Top := (S = '1');
    if S2 <> '' then ViaObj.IsTenting_Bottom := (S2 = '1');
  end;
  AddPrim(ViaObj);
  CountStat('via');
end;

function ShapeOf(S: String): Integer;
begin
  if S = 'R' then Result := eRounded
  else if S = 'S' then Result := eRectangular
  else if S = 'O' then Result := eOctagonal
  else if S = 'RR' then Result := eRoundedRectangular
  else Result := eRectangular;
end;

procedure SetPadCR(P: Variant; L: Integer; Pct: Double);
begin
  try
    P.StackCRPctOnLayer[L] := Round(Pct);
  except
  end;
end;

procedure DoPad;
var P, C: Variant; L, i: Integer; Shp: String; Slot: Integer; OX, OY: Integer;
begin
  P := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
  P.X := CX('x'); P.Y := CY('y');
  P.Name := V('name');
  L := LayerOf(V('layer'));
  P.Layer := L;
  if V('mode') = 'stack' then
  begin
    P.Mode := ePadMode_LocalStack;
    P.TopShape := ShapeOf(V('shape'));  P.TopXSize := VI('sx', 1); P.TopYSize := VI('sy', 1);
    P.MidShape := ShapeOf(V('mshape')); P.MidXSize := VI('msx', 1); P.MidYSize := VI('msy', 1);
    P.BotShape := ShapeOf(V('bshape')); P.BotXSize := VI('bsx', 1); P.BotYSize := VI('bsy', 1);
  end
  else
  begin
    P.Mode := ePadMode_Simple;
    P.TopShape := ShapeOf(V('shape')); P.TopXSize := VI('sx', 1); P.TopYSize := VI('sy', 1);
    P.MidShape := ShapeOf(V('shape')); P.MidXSize := VI('sx', 1); P.MidYSize := VI('sy', 1);
    P.BotShape := ShapeOf(V('shape')); P.BotXSize := VI('sx', 1); P.BotYSize := VI('sy', 1);
  end;
  if V('shape') = 'RR' then
  begin
    SetPadCR(P, eTopLayer, VD('cr', 50));
    SetPadCR(P, eBottomLayer, VD('cr', 50));
    SetPadCR(P, eMidLayer1, VD('cr', 50));
  end;
  if (V('mode') = 'stack') then
  begin
    if V('mshape') = 'RR' then SetPadCR(P, eMidLayer1, VD('mcr', 50));
    if V('bshape') = 'RR' then SetPadCR(P, eBottomLayer, VD('bcr', 50));
  end;
  if Has('hole') then
  begin
    P.HoleSize := VI('hole', 0);
    if Has('slot') then
    begin
      P.HoleType := eSlotHole;
      P.HoleWidth := VI('slot', 0);
      P.HoleRotation := VD('holerot', 0);
    end;
    P.Plated := VB('plated');
  end
  else
    P.HoleSize := 0;
  if Has('offx') then
  begin
    OX := VI('offx', 0); OY := VI('offy', 0);
    P.XPadOffset[eTopLayer] := OX;    P.YPadOffset[eTopLayer] := OY;
    P.XPadOffset[eBottomLayer] := OX; P.YPadOffset[eBottomLayer] := OY;
    for i := 0 to 29 do
    begin
      P.XPadOffset[eMidLayer1 + i] := OX;
      P.YPadOffset[eMidLayer1 + i] := OY;
    end;
  end;
  P.Rotation := VD('rot', 0);
  if VI('net', 0) > 0 then P.Net := NetOf('net');
  if Has('maskexp') or Has('pasteexp') or VB('nopaste') then
  begin
    C := P.GetState_Cache;
    if Has('maskexp') then
    begin
      C.SolderMaskExpansion := VI('maskexp', 0);
      C.SolderMaskExpansionValid := eCacheManual;
    end;
    if Has('pasteexp') then
    begin
      C.PasteMaskExpansion := VI('pasteexp', 0);
      C.PasteMaskExpansionValid := eCacheManual;
    end;
    if VB('nopaste') then
    begin
      C.PasteMaskExpansion := -(VI('sx', 1) + VI('sy', 1));
      C.PasteMaskExpansionValid := eCacheManual;
    end;
    P.SetState_Cache := C;
  end;
  if VB('tentT') then P.IsTenting_Top := True;
  if VB('tentB') then P.IsTenting_Bottom := True;
  AddPrim(P);
  CountStat('pad');
end;

{ ------------------------------------------------------------------ regions }
procedure DoRegionBegin;
begin
  RegObj := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
  RegObj.Layer := LayerOf(V('l'));
  if V('kind') = 'cutout' then RegObj.Kind := eRegionKind_Cutout
  else if V('kind') = 'boardcutout' then RegObj.Kind := eRegionKind_BoardCutout
  else RegObj.Kind := eRegionKind_Copper;
  if VI('net', 0) > 0 then RegObj.Net := NetOf('net');
  if Has('ko') then
  begin
    RegObj.IsKeepout := True;
    try
      RegObj.KeepoutRestrictions := VI('ko', 31);
    except
    end;
  end;
  RegGP := PCBServer.PCBGeometricPolygonFactory;
  RegC := PCBServer.PCBContourFactory;
  RegHole := False;
  RegPts := 0;
  InRegion := True;
end;

procedure RegionFlushContour;
begin
  if RegC.Count >= 3 then RegGP.AddContourIsHole(RegC, RegHole);
  RegC := PCBServer.PCBContourFactory;
end;

procedure DoRegionEnd;
begin
  RegionFlushContour;
  InRegion := False;
  if RegGP.Count = 0 then Exit;
  RegObj.SetGeometricPolygon(RegGP);
  AddPrim(RegObj);
  CountStat('region');
end;

{ ------------------------------------------------------------------ polygons / outline }
function MakeSeg(K: String; X, Y, CXv, CYv, R: Integer; A1, A2: Double): Variant;
begin
  Result := TPolySegment;
  if K = 'A' then Result.Kind := ePolySegmentArc else Result.Kind := ePolySegmentLine;
  Result.vx := X; Result.vy := Y;
  Result.cx := CXv; Result.cy := CYv;
  Result.Radius := R;
  Result.Angle1 := A1; Result.Angle2 := A2;
end;

procedure DoPolyBegin;
begin
  PolyObj := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
  PolyObj.Layer := LayerOf(V('l'));
  if VI('net', 0) > 0 then PolyObj.Net := NetOf('net');
  PolyObj.Name := V('name');
  if V('hatch') = '45' then PolyObj.PolyHatchStyle := ePolyHatch45
  else if V('hatch') = '90' then PolyObj.PolyHatchStyle := ePolyHatch90
  else PolyObj.PolyHatchStyle := ePolySolid;
  try
    if V('hatch') <> 'solid' then
    begin
      PolyObj.TrackSize := VI('track', 2500);
      PolyObj.Grid := VI('grid', 5000);
    end;
    PolyObj.MinTrack := VI('minw', 2500);
  except
    Err('polygon hatch settings: ' + ExceptionMessage);
  end;
  PolyObj.PourOver := ePolygonPourOver_SameNet;
  try
    { KiCad removes islands by connectivity (modes 0/1) or by area (mode 2); Altium's default
      area threshold (2500 sq mil) would also delete small KiCad zones }
    PolyObj.RemoveIslandsByArea := False;
    if VI('islands', 0) = 0 then PolyObj.RemoveDead := True
    else if VI('islands', 0) = 1 then PolyObj.RemoveDead := False
    else
    begin
      PolyObj.RemoveDead := False;
      PolyObj.RemoveIslandsByArea := True;
      { KiCad mm^2 -> Altium square internal units }
      PolyObj.IslandAreaThreshold := VD('islandarea', 0) * 393700.787 * 393700.787;
    end;
    { KiCad's filler removes copper narrower than min_thickness; Altium pours its own copper, so the
      counterpart is neck removal at that width (repour.pas can switch it off per polygon).
      KiCad prunes by deflating and re-inflating by min_thickness/2: thin tapers go, but a short pinch
      between two round clearances survives down to about 0.9 x min_thickness. Altium removes every neck
      below its threshold, so the pipeline re-pours a polygon at 0.9 x when that brings its copper area
      closer to KiCad's fill (e.g. an island behind a 0.281 mm pinch at 0.3 mm) }
    PolyObj.RemoveNarrowNecks := VB('neck');
    PolyObj.NeckWidthThreshold := VI('neckw', VI('minw', 2500));
    PolyObj.PourIndex := VI('order', 1);
    PolyObj.ArcApproximation := MilsToCoord(0.1);
  except
    Err('polygon settings: ' + ExceptionMessage);
  end;
  PolyPts.Clear;
  InPoly := True;
end;

procedure DoPolyEnd;
var i, n: Integer; S: String; X, Y, k: Integer;
begin
  InPoly := False;
  n := PolyPts.Count;
  if n < 3 then Exit;
  PolyObj.PointCount := n;
  for i := 0 to n do
  begin
    S := PolyPts[i mod n];
    k := Pos(',', S);
    X := StrToInt(Copy(S, 1, k - 1));
    Y := StrToInt(Copy(S, k + 1, 20));
    PolyObj.Segments[i] := MakeSeg('L', X, Y, 0, 0, 0, 0, 0);
  end;
  Board.AddPCBObject(PolyObj);
  CountStat('polygon');
end;

procedure DoOutlineEnd;
var BO: Variant; i, n: Integer; G: TStringList;
begin
  InOutline := False;
  n := OutlineSegs.Count;
  if n < 3 then Exit;
  BO := Board.BoardOutline;
  BO.BeginModify;
  BO.PointCount := n;
  G := TStringList.Create;
  G.Delimiter := #9; G.StrictDelimiter := True; G.QuoteChar := #1;
  for i := 0 to n do
  begin
    G.DelimitedText := OutlineSegs[i mod n];
    BO.Segments[i] := MakeSeg(G.Values['k'], StrToInt(G.Values['x']), StrToInt(G.Values['y']),
      StrToIntDef(G.Values['cx'], 0), StrToIntDef(G.Values['cy'], 0), StrToIntDef(G.Values['r'], 0),
      StrToIntDef(G.Values['a1'], 0) / 1000.0, StrToIntDef(G.Values['a2'], 0) / 1000.0);
  end;
  G.Free;
  BO.EndModify;
  BO.Invalidate;
  BO.Rebuild;
  BO.Validate;
  Log('board outline: ' + IntToStr(n) + ' segments');
end;



{ ------------------------------------------------------------------ stack / mech }
function CopperLayerObj(Idx: Integer): Variant;
var LS, LO: Variant; i: Integer;
begin
  LS := Board.LayerStack_V7;
  LO := LS.FirstLayer;
  i := 0;
  Result := Nil;
  while LO <> Nil do
  begin
    if i = Idx then
    begin
      Result := LO;
      Exit;
    end;
    i := i + 1;
    LO := LS.NextLayer(LO);
  end;
end;

procedure DoLayers;
var LS: Variant; n, i: Integer;
begin
  LS := Board.LayerStack_V7;
  n := VI('cu', 2);
  for i := 1 to n - 2 do
    LS.InsertLayer(eMidLayer1 + i - 1);
  Log('copper layers: ' + IntToStr(n));
end;

procedure DoCu;
var LO: Variant;
begin
  LO := CopperLayerObj(VI('i', 0));
  if LO = Nil then begin Err('copper layer index'); Exit; end;
  LO.Name := V('name');
  LO.CopperThickness := VI('th', 14000);
end;

procedure DoDiel;
var LO, D: Variant;
begin
  LO := CopperLayerObj(VI('i', 0));
  if LO = Nil then Exit;
  D := LO.Dielectric;
  D.DielectricHeight := VI('h', 126000);
  D.DielectricConstant := VD('er', 4.5);
  D.DielectricMaterial := V('mat');
  try
    D.DielectricLossTangent := VD('tand', 0.02);
  except
  end;
  if V('type') = 'prepreg' then D.DielectricType := ePrePreg else D.DielectricType := eCore;
end;

procedure DoMech;
var LS, M: Variant; n, p: Integer;
begin
  LS := Board.LayerStack_V7;
  n := VI('n', 1);
  M := LS.LayerObject_V7[ILayer.MechanicalLayer(n)];
  M.MechanicalLayerEnabled := True;
  M.Name := V('name');
  try
    M.Kind := VI('kind', 0);
  except
  end;
  p := VI('pair', 0);
  if (p > n) and (n <= 16) and (p <= 16) then
  begin
    try
      Board.MechanicalPairs.AddPair(eMechanical1 + n - 1, eMechanical1 + p - 1);
    except
      Err('mech pair: ' + ExceptionMessage);
    end;
  end;
end;

{ ------------------------------------------------------------------ components }
function KindOf(S: String): Integer;
begin
  if S = 'mechanical' then Result := eComponentKind_Mechanical
  else if S = 'graphical' then Result := eComponentKind_Graphical
  else if S = 'nettie_bom' then Result := eComponentKind_NetTie_BOM
  else if S = 'nettie_nobom' then Result := eComponentKind_NetTie_NoBOM
  else if S = 'standard_nobom' then Result := eComponentKind_Standard_NoBOM
  else Result := eComponentKind_Standard;
end;

procedure DoComp;
begin
  CurComp := PCBServer.PCBObjectFactory(eComponentObject, eNoDimension, eCreate_Default);
  CurComp.X := CX('x');
  CurComp.Y := CY('y');
  CurComp.Layer := eTopLayer;
  CurComp.Rotation := 0;
  CurComp.Pattern := V('pattern');
  CurComp.Name.Text := V('des');
  CurComp.Comment.Text := V('comment');
  try
    CurComp.ComponentKind := KindOf(V('kind'));
    CurComp.SourceDesignator := V('des');
    CurComp.SourceFootprintLibrary := V('fplib');
    if V('uid') <> '' then CurComp.SourceUniqueId := V('uid');
    if V('libref') <> '' then CurComp.SourceLibReference := V('libref');
    if V('complib') <> '' then CurComp.SourceComponentLibrary := V('complib');
    CurComp.FootprintDescription := V('desc');
    CurComp.Height := VI('h', 0);
  except
    Err('component properties: ' + ExceptionMessage);
  end;
  Board.AddPCBObject(CurComp);
  InComp := True;
end;

procedure SetCompHeight;
var It, B: Variant; H: Integer;
begin
  H := 0;
  It := CurComp.GroupIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
  B := It.FirstPCBObject;
  while B <> Nil do
  begin
    if B.OverallHeight > H then H := B.OverallHeight;
    B := It.NextPCBObject;
  end;
  CurComp.GroupIterator_Destroy(It);
  if H > 0 then CurComp.Height := H;
end;

procedure DoCompEnd;
var X, Y: Integer;
begin
  InComp := False;
  try
    SetCompHeight;
  except
  end;
  X := CX('x'); Y := CY('y');
  if VB('flip') then
  begin
    CurComp.Layer := eBottomLayer;
    CurComp.MoveToXY(X, Y);
  end;
  if VB('flip') or (VD('rot', 0) <> 0) then CurComp.Rotation := VD('rot', 0);
  if (CurComp.X <> X) or (CurComp.Y <> Y) then CurComp.MoveToXY(X, Y);
  CountStat('component');
end;

procedure DoCheckPad;
var It, P: Variant; d, best, bdx, bdy: Integer; Nm: String;
begin
  Nm := V('pad');
  best := -1;
  It := CurComp.GroupIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePadObject));
  P := It.FirstPCBObject;
  while P <> Nil do
  begin
    if P.Name = Nm then
    begin
      d := Abs(P.X - CX('x')) + Abs(P.Y - CY('y'));
      if (best < 0) or (d < best) then
      begin
        best := d; bdx := P.X - CX('x'); bdy := P.Y - CY('y');
      end;
    end;
    P := It.NextPCBObject;
  end;
  CurComp.GroupIterator_Destroy(It);
  if best < 0 then Exit;
  if best > 400 then
    Log('PADCHECK ' + V('des') + ' pad ' + Nm + ' off by ' + IntToStr(bdx) + ',' + IntToStr(bdy) + ' rot=' + FloatToStr(CurComp.Rotation) + ' layer=' + IntToStr(CurComp.Layer))
  else
    CountStat('padcheck_ok');
end;

procedure DoBody;
var B, M: Variant; FN: String;
begin
  FN := V('file');
  if not FileExists(FN) then begin Err('STEP not found: ' + FN); Exit; end;
  B := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
  B.BodyProjection := eBoardSide_Top;
  B.Layer := LayerOf(V('l'));
  M := B.ModelFactory_FromFilename(FN, False);
  if M = Nil then begin Err('cannot load STEP ' + FN); Exit; end;
  M.SetState(VD('rx', 0), VD('ry', 0), VD('rz', 0), VI('dz', 0));
  B.Model := M;
  B.SetState_FromModel;
  try
    B.BodyOpacity3D := VD('opacity', 1);
    B.SetState_Identifier(ChangeFileExt(ExtractFileName(FN), ''));
  except
  end;
  B.MoveByXY(CX('x'), CY('y'));
  AddPrim(B);
  CountStat('body');
end;

procedure DoXBody;
var B, C, M: Variant;
begin
  B := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
  B.Layer := LayerOf(V('l'));
  B.BodyProjection := eBoardSide_Top;
  C := PCBServer.PCBContourFactory;
  C.AddPoint(CX('x1'), CY('y1')); C.AddPoint(CX('x2'), CY('y1'));
  C.AddPoint(CX('x2'), CY('y2')); C.AddPoint(CX('x1'), CY('y2'));
  B.SetOutlineContour(C);
  M := B.ModelFactory_CreateExtruded(0, VI('h', 393700), $909090);
  B.Model := M;
  B.StandoffHeight := 0;
  B.OverallHeight := VI('h', 393700);
  try
    B.SetState_Identifier('Convi placeholder');
  except
  end;
  AddPrim(B);
  CountStat('placeholder');
end;

{ ------------------------------------------------------------------ nets / docs }
procedure DoNet;
var N: Variant;
begin
  N := PCBServer.PCBObjectFactory(eNetObject, eNoDimension, eCreate_Default);
  N.Name := V('name');
  Board.AddPCBObject(N);
  Nets.Add(N);
  NetNames.Add(V('name'));
end;

function NetByName(Nm: String): Variant;
var i: Integer;
begin
  Result := Nil;
  i := NetNames.IndexOf(Nm);
  if i >= 0 then Result := Nets.Items[i];
end;

{ ------------------------------------------------------------------ classes / rules }
procedure DoClass;
var K: String; MK: Integer;
begin
  K := V('kind');
  if K = 'pad' then MK := eClassMemberKind_Pad
  else if K = 'comp' then MK := eClassMemberKind_Component
  else if K = 'dp' then MK := eClassMemberKind_DifferentialPair
  else MK := eClassMemberKind_Net;
  CurClass := PCBServer.PCBClassFactoryByClassMember(MK);
  CurClass.SuperClass := False;
  CurClass.Name := V('name');
  Board.AddPCBObject(CurClass);
  CountStat('class');
end;

procedure DoClassMember;
begin
  CurClass.AddMemberByName(V('member'));
end;

procedure DoDiffPair;
var D, P, N: Variant;
begin
  P := NetByName(V('p'));
  N := NetByName(V('n'));
  if (P = Nil) or (N = Nil) then begin Err('diff pair nets not found: ' + V('p')); Exit; end;
  D := PCBServer.PCBObjectFactory(eDifferentialPairObject, eNoDimension, eCreate_Default);
  D.Name := V('name');
  D.PositiveNet := P;
  D.NegativeNet := N;
  Board.AddPCBObject(D);
  CountStat('diffpair');
end;

function RuleKindOf(K: String): Integer;
begin
  if K = 'clearance' then Result := eRule_Clearance
  else if K = 'width' then Result := eRule_MaxMinWidth
  else if K = 'vias' then Result := eRule_RoutingViaStyle
  else if K = 'diffpair' then Result := eRule_DifferentialPairsRouting
  else if K = 'polyconnect' then Result := eRule_PolygonConnectStyle
  else if K = 'holesize' then Result := eRule_MaxMinHoleSize
  else if K = 'holetohole' then Result := eRule_HoleToHoleClearance
  else if K = 'annular' then Result := eRule_MinimumAnnularRing
  else if K = 'maskexp' then Result := eRule_SolderMaskExpansion
  else if K = 'pasteexp' then Result := eRule_PasteMaskExpansion
  else if K = 'sliver' then Result := eRule_MinimumSolderMaskSliver
  else if K = 'silkmask' then Result := eRule_SilkToSolderMaskClearance
  else if K = 'silksilk' then Result := eRule_SilkToSilkClearance
  else if K = 'compclear' then Result := eRule_ComponentClearance
  else if K = 'outline' then Result := eRule_BoardOutlineClearance
  else if K = 'creepage' then Result := eRule_Creepage
  else if (K = 'length') or (K = 'matched') or (K = 'routinglayers') or (K = 'maxvias') or (K = 'room')
    or (K = 'short') then
  begin
    try
      if K = 'short' then Result := eRule_ShortCircuit
      else if K = 'length' then Result := eRule_MaxMinLength
      else if K = 'matched' then Result := eRule_MatchedLengths
      else if K = 'routinglayers' then Result := eRule_RoutingLayers
      else if K = 'maxvias' then Result := eRule_MaximumViaCount
      else Result := eRule_ConfinementConstraint;
    except
      { TRuleKind ordinals of Altium 26 (ShortCircuit read back as 15 on AD24 too) }
      if K = 'short' then Result := 15
      else if K = 'length' then Result := 3
      else if K = 'matched' then Result := 4
      else if K = 'routinglayers' then Result := 9
      else if K = 'maxvias' then Result := 18
      else Result := 22;
    end;
  end
  else Result := -1;
end;

procedure DoRuleClear;
var It, R: Variant; Del: TInterfaceList; i: Integer; Ks: TStringList;
begin
  Ks := TStringList.Create;
  Ks.CommaText := V('kinds');
  for i := 0 to Ks.Count - 1 do Ks[i] := IntToStr(RuleKindOf(Ks[i]));
  Del := TInterfaceList.Create;
  It := Board.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eRuleObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  R := It.FirstPCBObject;
  while R <> Nil do
  begin
    if Ks.IndexOf(IntToStr(R.RuleKind)) >= 0 then Del.Add(R)
    { Altium default rules KiCad has no counterpart for, by name (Height: KiCad checks no part height).
      Deleting one does not hold (Altium puts the default rule of a kind back), so it is switched off }
    else if Pos('|' + R.Name + '|', '|' + V('names') + '|') > 0 then
    begin
      try
        R.DRCEnabled := False;
        R.Comment := 'Convi: off - KiCad has no counterpart for this check';
      except
      end;
      try
        R.Enabled := False;
      except
      end;
    end;
    R := It.NextPCBObject;
  end;
  Board.BoardIterator_Destroy(It);
  for i := 0 to Del.Count - 1 do Board.RemovePCBObject(Del.Items[i]);
  Log('removed default rules: ' + IntToStr(Del.Count));
end;

procedure SetPerLayer(R: Variant; Prop: String; Val: Integer);
var L: Integer;
begin
  for L := eTopLayer to eBottomLayer do
  begin
    if Prop = 'MinWidth' then R.MinWidth[L] := Val
    else if Prop = 'MaxWidth' then R.MaxWidth[L] := Val
    else if Prop = 'FavoredWidth' then R.FavoredWidth[L] := Val
    else if Prop = 'PreferedWidth' then R.PreferedWidth[L] := Val
    else if Prop = 'MinGap' then R.MinGap[L] := Val
    else if Prop = 'MaxGap' then R.MaxGap[L] := Val
    else if Prop = 'PreferedGap' then R.PreferedGap[L] := Val;
  end;
end;

procedure SetDiffPairLayers(R: Variant; Spec: String);
{ Spec: 'code:minw:prefw:maxw:mingap:prefgap:maxgap;...' - a KiCad custom rule with a layer condition
  (e.g. inner and outer layers with their own pair gap) }
var Items, F: TStringList; i, L: Integer;
begin
  Items := TStringList.Create;
  F := TStringList.Create;
  try
    Items.Delimiter := ';';
    Items.StrictDelimiter := True;
    Items.DelimitedText := Spec;
    F.Delimiter := ':';
    F.StrictDelimiter := True;
    for i := 0 to Items.Count - 1 do
    begin
      F.DelimitedText := Items[i];
      if F.Count = 7 then
      begin
        L := LayerOf(F[0]);
        R.MinWidth[L] := StrToInt(F[1]);
        R.PreferedWidth[L] := StrToInt(F[2]);
        R.MaxWidth[L] := StrToInt(F[3]);
        R.MinGap[L] := StrToInt(F[4]);
        R.PreferedGap[L] := StrToInt(F[5]);
        R.MaxGap[L] := StrToInt(F[6]);
      end;
    end;
  finally
    F.Free;
    Items.Free;
  end;
end;

procedure SetRoutingLayers(R: Variant; Codes: String);
var L, i: Integer; Ks: TStringList;
begin
  for L := eTopLayer to eBottomLayer do R.RoutingLayers[L] := False;
  Ks := TStringList.Create;
  Ks.CommaText := Codes;
  for i := 0 to Ks.Count - 1 do
    if (Ks[i] <> '') and (Ks[i] <> '-') then R.RoutingLayers[LayerOf(Ks[i])] := True;
  Ks.Free;
end;

procedure DoRule;
var R: Variant; K, St: String; Kind: Integer;
begin
  K := V('kind');
  Kind := RuleKindOf(K);
  if Kind < 0 then begin Err('unknown rule kind ' + K); Exit; end;
  R := PCBServer.PCBRuleFactory(Kind);
  R.Name := V('name');
  if Has('comment') then R.Comment := 'Convi: ' + V('comment')
  else R.Comment := 'Converted from KiCad by Convi';
  if Has('s1') then R.Scope1Expression := V('s1');
  if Has('s2') then R.Scope2Expression := V('s2');
  if (K = 'clearance') or (K = 'holetohole') or (K = 'outline') then R.Gap := VI('gap', 0)
  else if K = 'creepage' then
  begin
    { Scripts cannot set the creepage distance: Convi writes CHECKDISTANCE into the saved PcbDoc
      (pcbdoc_patch.py) and rewrites this comment; if that fails the comment says what to enter }
    R.Comment := R.Comment + ' - set Creepage Distance to ' + V('label');
  end
  else if K = 'room' then
  begin
    { KiCad courtyard used by intersectsCourtyard(): a room only for TouchesRoom(), never a DRC check }
    try
      R.Kind := eConfineIn;
      R.Layer := LayerOf(V('l'));
      R.BoundingRect := RectToCoordRect(Rect(VI('x1', 0), VI('y2', 0), VI('x2', 0), VI('y1', 0)));
    except
      Err('room ' + V('name') + ': cannot set its region');
    end;
    try
      R.DRCEnabled := False;
    except
    end;
  end
  else if K = 'length' then
  begin
    try
      R.MinLimit := VI('min', 0);
      R.MaxLimit := VI('max', 0);
    except
      Err('length rule ' + V('name') + ': cannot set limits');
    end;
  end
  else if K = 'matched' then
  begin
    try
      R.Tolerance := VI('tol', 0);
    except
      Err('matched length rule ' + V('name') + ': cannot set tolerance');
    end;
  end
  else if K = 'maxvias' then
  begin
    try
      R.Limit := VI('limit', 0);
    except
      Err('via count rule ' + V('name') + ': cannot set limit');
    end;
  end
  else if K = 'routinglayers' then
  begin
    try
      SetRoutingLayers(R, V('allow'));
    except
      Err('routing layers rule ' + V('name') + ': cannot set layers');
    end;
  end
  else if K = 'width' then
  begin
    SetPerLayer(R, 'MaxWidth', VI('max', 0));
    SetPerLayer(R, 'MinWidth', VI('min', 0));
    SetPerLayer(R, 'FavoredWidth', VI('pref', 0));
  end
  else if K = 'vias' then
  begin
    R.MaxWidth := VI('max', 0); R.MinWidth := VI('min', 0); R.PreferedWidth := VI('pref', 0);
    R.MaxHoleWidth := VI('hmax', 0); R.MinHoleWidth := VI('hmin', 0); R.PreferedHoleWidth := VI('hpref', 0);
  end
  else if K = 'diffpair' then
  begin
    SetPerLayer(R, 'MaxWidth', VI('maxw', 0));
    SetPerLayer(R, 'MinWidth', VI('minw', 0));
    SetPerLayer(R, 'PreferedWidth', VI('prefw', 0));
    SetPerLayer(R, 'MaxGap', VI('maxgap', 0));
    SetPerLayer(R, 'MinGap', VI('mingap', 0));
    SetPerLayer(R, 'PreferedGap', VI('prefgap', 0));
    if Has('perlayer') then
    begin
      try
        SetDiffPairLayers(R, V('perlayer'));
      except
        Err('diff pair rule ' + V('name') + ': per-layer width/gap not set');
      end;
    end;
    if Has('uncoupled') then
    begin
      try
        R.MaxUncoupledLength := VI('uncoupled', 0);
      except
        Err('diff pair rule ' + V('name') + ': cannot set max uncoupled length');
      end;
    end;
  end
  else if K = 'polyconnect' then
  begin
    St := V('style');
    if St = 'direct' then R.ConnectStyle := eDirectConnectToPlane
    else if St = 'none' then R.ConnectStyle := eNoConnect
    else R.ConnectStyle := eReliefConnectToPlane;
    R.ReliefConductorWidth := VI('width', 100000);
    R.ReliefAirGap := VI('gap', 100000);
    R.ReliefEntries := VI('entries', 4);
    if VI('angle', 90) = 45 then R.PolygonReliefAngle := ePolygonReliefAngle_45
    else R.PolygonReliefAngle := ePolygonReliefAngle_90;
  end
  else if K = 'holesize' then
  begin
    R.AbsoluteValues := True;
    R.MaxLimit := VI('max', 0);
    R.MinLimit := VI('min', 0);
  end
  else if K = 'annular' then R.Minimum := VI('min', 0)
  else if (K = 'maskexp') or (K = 'pasteexp') then R.Expansion := VI('exp', 0)
  else if K = 'sliver' then R.MinSolderMaskSliver := VI('min', 0)
  else if K = 'silkmask' then R.SilkToMaskGap := VI('gap', 0)
  else if K = 'silksilk' then R.SilkToSilkClearance := VI('gap', 0)
  else if K = 'compclear' then
  begin
    R.Gap := VI('gap', 0);
    R.VerticalGap := VI('vgap', 0);
  end;
  if VI('nodrc', 0) = 1 then
  begin
    { the KiCad project switched this check off (rule_severities 'ignore'): rule kept, out of DRC }
    try
      R.DRCEnabled := False;
    except
    end;
  end;
  Board.AddPCBObject(R);
  CountStat('rule');
end;

procedure CloseIfOpen(Path: String);
var D: Variant;
begin
  try
    D := Client.GetDocumentByPath(Path);
    if D <> Nil then
    begin
      { Convi rewrites this file: drop unsaved edits so Altium does not ask "Save changes?" }
      D.SetModified(False);
      Client.CloseDocument(D);
    end;
  except
  end;
end;

procedure DoNewPcb;
begin
  CloseIfOpen(V('path'));
  Mode := 'PCB';
  OffX := 0; OffY := 0;
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  Doc.SetFileName(V('path'));
  PCBServer.PreProcess;
  Log('new PCB ' + V('path'));
end;

procedure DoSave;
begin
  PCBServer.PostProcess;
  { polygons are poured by the separate repour.pas pass (needs committed board state and a clean,
    priority-ordered pour) }
  try
    Board.ViewManager_FullUpdate;
  except
  end;
  Doc.DoFileSave('');
  Log('saved ' + Doc.FileName);
  if VB('close') then Client.CloseDocument(Doc);
end;

procedure DoNewLib;
var It: Variant;
begin
  Mode := 'LIB';
  CloseIfOpen(V('path'));
  if FileExists(V('path')) then DeleteFile(V('path'));
  Doc := Client.OpenNewDocument('PCBLIB', V('path'), '', False);
  Client.ShowDocument(Doc);
  PcbLib := PCBServer.GetCurrentPCBLibrary;
  Board := PcbLib.Board;
  OffX := Board.XOrigin;
  OffY := Board.YOrigin;
  It := PcbLib.LibraryIterator_Create;
  It.SetState_FilterAll;
  DefaultLibComp := It.FirstPCBObject;
  PcbLib.LibraryIterator_Destroy(It);
  FirstLibFp := True;
  PCBServer.PreProcess;
  Log('new PcbLib ' + V('path'));
end;

procedure DoFp;
begin
  CurLibComp := PCBServer.CreatePCBLibComp;
  CurLibComp.Name := V('name');
  PcbLib.RegisterComponent(CurLibComp);
  try
    CurLibComp.Description := V('desc');
    CurLibComp.Height := VI('h', 0);
  except
  end;
  if FirstLibFp and (DefaultLibComp <> Nil) then
  begin
    try
      PcbLib.RemoveComponent(DefaultLibComp);
    except
    end;
    FirstLibFp := False;
  end;
end;

procedure DoFpEnd;
begin
  PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, CurLibComp.I_ObjectAddress);
  CountStat('footprint');
end;

procedure DoSaveLib;
begin
  PCBServer.PostProcess;
  try
    PcbLib.CurrentComponent := CurLibComp;
    Board.ViewManager_FullUpdate;
  except
  end;
  Doc.DoFileSave('PcbLib');
  Log('saved library ' + Doc.FileName);
  if VB('close') then Client.CloseDocument(Doc);
end;

procedure DoOrigin;
begin
  Board.XOrigin := VI('x', 0);
  Board.YOrigin := VI('y', 0);
end;

{ ------------------------------------------------------------------ dispatcher }
procedure Exec(Cmd: String; Line: String);
var k: Integer;
begin
  if Cmd = 'P' then
  begin
    if InRegion then
      RegC.AddPoint(StrToInt(F[1]) + OffX, StrToInt(F[2]) + OffY)
    else if InPoly then
      PolyPts.Add(F[1] + ',' + F[2]);
  end
  else if Cmd = 'TRACK' then DoTrack
  else if Cmd = 'PAD' then DoPad
  else if Cmd = 'ARC' then DoArc
  else if Cmd = 'VIA' then DoVia
  else if Cmd = 'TEXT' then DoText
  else if Cmd = 'REGION' then DoRegionBegin
  else if Cmd = 'HOLE' then begin RegionFlushContour; RegHole := True; end
  else if Cmd = 'REGIONEND' then DoRegionEnd
  else if Cmd = 'COMP' then DoComp
  else if Cmd = 'COMPEND' then DoCompEnd
  else if Cmd = 'COMPTEXT' then DoCompText
  else if Cmd = 'CHECKPAD' then DoCheckPad
  else if Cmd = 'COMPPARAM' then begin end
  else if Cmd = 'BODY' then DoBody
  else if Cmd = 'XBODY' then DoXBody
  else if Cmd = 'POLY' then DoPolyBegin
  else if Cmd = 'POLYEND' then DoPolyEnd
  else if Cmd = 'OUTLINE' then begin OutlineSegs.Clear; InOutline := True; end
  else if Cmd = 'OSEG' then OutlineSegs.Add(Line)
  else if Cmd = 'OUTLINEEND' then DoOutlineEnd
  else if Cmd = 'NET' then DoNet
  else if Cmd = 'CLASS' then DoClass
  else if Cmd = 'CLASSM' then DoClassMember
  else if Cmd = 'DIFFPAIR' then DoDiffPair
  else if Cmd = 'RULECLEAR' then DoRuleClear
  else if Cmd = 'RULE' then DoRule
  else if Cmd = 'LAYERS' then DoLayers
  else if Cmd = 'CU' then DoCu
  else if Cmd = 'DIEL' then DoDiel
  else if Cmd = 'MASK' then begin end
  else if Cmd = 'MECH' then DoMech
  else if Cmd = 'ORIGIN' then DoOrigin
  else if Cmd = 'REPOUR' then begin end { polygons are poured by repour.pas }
  else if Cmd = 'NEWPCB' then DoNewPcb
  else if Cmd = 'SAVE' then DoSave
  else if Cmd = 'NEWLIB' then DoNewLib
  else if Cmd = 'FP' then DoFp
  else if Cmd = 'FPEND' then DoFpEnd
  else if Cmd = 'SAVELIB' then DoSaveLib
  else Err('unknown command ' + Cmd);
end;

