procedure LogRect(Tag: String; R: TCoordRect);
begin
  L(Tag + ' L=' + FloatToStr(CoordToMMs(R.Left)) + ' B=' + FloatToStr(CoordToMMs(R.Bottom)) + ' R=' + FloatToStr(CoordToMMs(R.Right)) + ' T=' + FloatToStr(CoordToMMs(R.Top)));
end;

procedure Body;
var
  Doc, Board, T, BO, Seg, P, Rg, C, GP, A, V, F, Net: Variant;
  i: Integer;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  PCBServer.PreProcess;

  // --- frame text
  T := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
  T.XLocation := MMsToCoord(10); T.YLocation := MMsToCoord(10);
  T.Layer := eTopOverlay;
  T.Text := 'Hello';
  T.Size := MMsToCoord(1);
  T.Width := MMsToCoord(0.15);
  Board.AddPCBObject(T);
  LogRect('plain text', T.BoundingRectangle);
  try
    T.Multiline := True;
    T.MultilineTextWidth := MMsToCoord(10);
    T.MultilineTextHeight := MMsToCoord(2);
    T.MultilineTextAutoPosition := eAutoPos_CenterCenter;
    T.XLocation := MMsToCoord(10); T.YLocation := MMsToCoord(10);
    LogRect('frame text CC', T.BoundingRectangle);
    T.MultilineTextAutoPosition := eAutoPos_BottomLeft;
    LogRect('frame text BL', T.BoundingRectangle);
  except
    L('frame failed: ' + ExceptionMessage);
  end;
  // TrueType
  T := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
  T.XLocation := MMsToCoord(10); T.YLocation := MMsToCoord(20);
  T.Layer := eTopOverlay; T.Text := 'TTF'; T.Size := MMsToCoord(1.5);
  T.UseTTFonts := True; T.FontName := 'Arial'; T.Bold := True;
  Board.AddPCBObject(T);
  LogRect('ttf text', T.BoundingRectangle);

  // --- board outline
  try
    BO := Board.BoardOutline;
    BO.BeginModify;
    BO.PointCount := 4;
    Seg := TPolySegment;
    Seg.Kind := ePolySegmentLine; Seg.vx := MMsToCoord(0); Seg.vy := MMsToCoord(0); BO.Segments[0] := Seg;
    Seg.Kind := ePolySegmentLine; Seg.vx := MMsToCoord(50); Seg.vy := MMsToCoord(0); BO.Segments[1] := Seg;
    Seg.Kind := ePolySegmentArc; Seg.vx := MMsToCoord(50); Seg.vy := MMsToCoord(30);
    Seg.cx := MMsToCoord(50); Seg.cy := MMsToCoord(15); Seg.Radius := MMsToCoord(15); Seg.Angle1 := 270; Seg.Angle2 := 90;
    BO.Segments[2] := Seg;
    Seg.Kind := ePolySegmentLine; Seg.vx := MMsToCoord(0); Seg.vy := MMsToCoord(30); BO.Segments[3] := Seg;
    BO.EndModify;
    BO.Invalidate; BO.Rebuild; BO.Validate;
    LogRect('board outline', BO.BoundingRectangle);
  except
    L('outline failed: ' + ExceptionMessage);
  end;

  // --- net
  Net := PCBServer.PCBObjectFactory(eNetObject, eNoDimension, eCreate_Default);
  Net.Name := 'GND';
  Board.AddPCBObject(Net);

  // --- polygon
  try
    P := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);
    P.Layer := eTopLayer;
    P.PolyHatchStyle := ePolySolid;
    P.PointCount := 4;
    Seg := TPolySegment;
    Seg.Kind := ePolySegmentLine; Seg.vx := MMsToCoord(5); Seg.vy := MMsToCoord(5); P.Segments[0] := Seg;
    Seg.vx := MMsToCoord(45); Seg.vy := MMsToCoord(5); P.Segments[1] := Seg;
    Seg.vx := MMsToCoord(45); Seg.vy := MMsToCoord(25); P.Segments[2] := Seg;
    Seg.vx := MMsToCoord(5); Seg.vy := MMsToCoord(25); P.Segments[3] := Seg;
    P.Net := Net;
    P.Name := 'GND_TOP';
    P.PourOver := ePolygonPourOver_SameNet;
    P.RemoveDead := True;
    Board.AddPCBObject(P);
    P.Rebuild;
    L('polygon ok area=' + FloatToStr(P.AreaSize));
  except
    L('polygon failed: ' + ExceptionMessage);
  end;

  // --- region with hole
  try
    Rg := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    Rg.Layer := eBottomLayer;
    GP := PCBServer.PCBGeometricPolygonFactory;
    C := PCBServer.PCBContourFactory;
    C.AddPoint(MMsToCoord(10), MMsToCoord(10)); C.AddPoint(MMsToCoord(20), MMsToCoord(10));
    C.AddPoint(MMsToCoord(20), MMsToCoord(20)); C.AddPoint(MMsToCoord(10), MMsToCoord(20));
    GP.AddContourIsHole(C, False);
    C := PCBServer.PCBContourFactory;
    C.AddPoint(MMsToCoord(12), MMsToCoord(12)); C.AddPoint(MMsToCoord(14), MMsToCoord(12));
    C.AddPoint(MMsToCoord(14), MMsToCoord(14)); C.AddPoint(MMsToCoord(12), MMsToCoord(14));
    GP.AddContourIsHole(C, True);
    Rg.SetGeometricPolygon(GP);
    Rg.Net := Net;
    Board.AddPCBObject(Rg);
    L('region holes=' + IntToStr(Rg.HoleCount) + ' area=' + FloatToStr(Rg.Area));
  except
    L('region failed: ' + ExceptionMessage);
  end;
  // keepout region
  try
    Rg := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    Rg.Layer := eTopLayer;
    C := PCBServer.PCBContourFactory;
    C.AddPoint(MMsToCoord(30), MMsToCoord(10)); C.AddPoint(MMsToCoord(35), MMsToCoord(10));
    C.AddPoint(MMsToCoord(35), MMsToCoord(15));
    Rg.SetOutlineContour(C);
    Rg.IsKeepout := True;
    Board.AddPCBObject(Rg);
    L('keepout ok restrictions=' + IntToStr(Rg.KeepoutRestrictions));
  except
    L('keepout failed: ' + ExceptionMessage);
  end;
  // board cutout
  try
    Rg := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    Rg.Layer := eMultiLayer;
    Rg.Kind := eRegionKind_BoardCutout;
    C := PCBServer.PCBContourFactory;
    C.AddPoint(MMsToCoord(40), MMsToCoord(20)); C.AddPoint(MMsToCoord(42), MMsToCoord(20));
    C.AddPoint(MMsToCoord(42), MMsToCoord(22)); C.AddPoint(MMsToCoord(40), MMsToCoord(22));
    Rg.SetOutlineContour(C);
    Board.AddPCBObject(Rg);
    L('board cutout ok');
  except
    L('cutout failed: ' + ExceptionMessage);
  end;
  // arc
  A := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
  A.XCenter := MMsToCoord(25); A.YCenter := MMsToCoord(15); A.Radius := MMsToCoord(3);
  A.LineWidth := MMsToCoord(0.2); A.StartAngle := 0; A.EndAngle := 90; A.Layer := eTopLayer;
  Board.AddPCBObject(A);
  L('arc ok');
  // via blind
  V := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
  V.X := MMsToCoord(25); V.Y := MMsToCoord(25); V.Size := MMsToCoord(0.6); V.HoleSize := MMsToCoord(0.3);
  V.LowLayer := eBottomLayer; V.HighLayer := eTopLayer; V.Net := Net;
  Board.AddPCBObject(V);
  L('via ok');
  F := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
  F.X1Location := MMsToCoord(1); F.Y1Location := MMsToCoord(1); F.X2Location := MMsToCoord(3); F.Y2Location := MMsToCoord(2);
  F.Rotation := 0; F.Layer := eTopLayer;
  Board.AddPCBObject(F);
  L('fill ok');
  PCBServer.PostProcess;
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p08.PcbDoc');
  Doc.DoFileSave('');
  L('saved');
end;
